"""run_experiment against a minimal local stand-in for the Traceforge experiment endpoints."""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from traceforge import Client
from traceforge._evaluate import run_experiment


class FakeTraceforge:
    def __init__(self, baseline_status: str = "pending") -> None:
        self.calls: list[tuple[str, str]] = []
        self.submitted: dict[str, list[dict[str, Any]]] = {"run-b": [], "run-c": []}
        self.ingested: list[dict[str, Any]] = []
        self.arms = {"baseline": {"run_id": "run-b", "run_status": baseline_status, "config": {"retriever": "keyword"}},
                     "candidate": {"run_id": "run-c", "run_status": "pending", "config": {"retriever": "stemmed"}}}

    def handle(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.calls.append((method, path))
        if path == "/api/v1/ingest/batch":
            self.ingested.append(body)
            return 202, {"accepted_traces": len(body["traces"]), "accepted_spans": len(body["spans"])}
        if path == "/api/v1/sdk/experiments/exp-1/start":
            return 200, {"id": "exp-1", "name": "retriever", "status": "running", **self.arms}
        if path == "/api/v1/sdk/experiments/exp-1/complete":
            return 200, {"id": "exp-1", "status": "completed", "comparison": {"suggested_outcome": "candidate_improves"}}
        for run in ("run-b", "run-c"):
            if path == f"/api/v1/sdk/eval-runs/{run}":
                return 200, {"id": run, "name": run, "status": "pending", "dataset_name": "QA", "dataset_version": 1,
                             "cases": [{"case_id": f"{run}-c1", "key": "q1", "input": {"question": "q1"}, "metadata": {}},
                                       {"case_id": f"{run}-c2", "key": "q2", "input": {"question": "q2"}, "metadata": {}}]}
            if path == f"/api/v1/sdk/eval-runs/{run}/results":
                self.submitted[run] += body["results"]
                return 200, {"accepted": len(body["results"]), "status": "running", "received_cases": 2, "total_cases": 2}
            if path == f"/api/v1/sdk/eval-runs/{run}/finish":
                return 200, {"id": run, "status": "completed", "pass_rate": 1.0, "summary": {}}
        return 404, {"error": {"code": "not_found", "message": "Not found."}}


@pytest.fixture
def server() -> Any:
    fake = FakeTraceforge()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            status, payload = fake.handle(self.command, self.path, body)
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = _respond

        def log_message(self, *args: Any) -> None:
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield fake, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_each_arm_runs_every_case_with_its_own_config_and_labelled_traces(server: Any) -> None:
    fake, url = server
    client = Client("tf_key", url, flush_interval=0.05)
    seen: list[tuple[str, str]] = []

    def task(case_input: dict, config: dict) -> dict:
        seen.append((case_input["question"], config["retriever"]))
        return {"answer": f"{case_input['question']} via {config['retriever']}"}

    result = run_experiment(client, "exp-1", task)
    client.shutdown(5)
    assert result["status"] == "completed"
    assert seen == [("q1", "keyword"), ("q2", "keyword"), ("q1", "stemmed"), ("q2", "stemmed")]
    assert [r["output"]["answer"] for r in fake.submitted["run-c"]] == ["q1 via stemmed", "q2 via stemmed"]
    assert all(r["trace_id"] for r in fake.submitted["run-b"] + fake.submitted["run-c"])
    traces = [t for batch in fake.ingested for t in batch["traces"]]
    assert {t["metadata"]["arm"] for t in traces} == {"baseline", "candidate"}
    assert all({"evaluation", "experiment"} <= set(t["tags"]) and t["metadata"]["experiment_id"] == "exp-1" for t in traces)
    assert fake.calls.index(("POST", "/api/v1/sdk/experiments/exp-1/start")) == 0
    assert fake.calls[-1] == ("POST", "/api/v1/sdk/experiments/exp-1/complete")


def test_async_task_and_resuming_with_a_finished_arm(server: Any) -> None:
    fake, url = server
    fake.arms["baseline"]["run_status"] = "completed"  # already executed earlier
    client = Client("tf_key", url, flush_interval=0.05)

    async def task(case_input: dict, config: dict) -> dict:
        await asyncio.sleep(0)
        return {"answer": config["retriever"]}

    run_experiment(client, "exp-1", task)
    client.shutdown(5)
    assert fake.submitted["run-b"] == []  # the scored arm was not re-run
    assert [r["output"] for r in fake.submitted["run-c"]] == [{"answer": "stemmed"}] * 2
