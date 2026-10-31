"""Evaluation runs: execution through the SDK API, scoring, the state machine, immutability and isolation."""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import EvaluationResult, EvaluationRun
from app.services import evaluators as ev
from tests.conftest import ApiClient

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
EVALUATORS = [
    {"type": "normalized_match", "field": "answer"},
    {"type": "source_match"},
    {"type": "required_fields", "fields": ["answer"]},
]
CASES = [
    {"key": "shipping", "input": {"question": "Where is order A100?"}, "expected_output": "Your order is shipped.",
     "expected_sources": ["kb/shipping.md"]},
    {"key": "refunds", "input": {"question": "How do refunds work?"}, "expected_output": "Refunds take 5 days.",
     "expected_sources": ["kb/returns.md"]},
    {"key": "warranty", "input": {"question": "Warranty?"}, "expected_output": "12 months."},
]


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


class Env:
    def __init__(self, alice: ApiClient, client: ApiClient) -> None:
        self.alice, self.client = alice, client
        self.project = alice.create_project("Agent", slug="agent")
        self.key = alice.create_api_key(self.project["id"])["key"]
        self.base = f"/api/v1/projects/{self.project['id']}"
        dataset = alice.post(f"{self.base}/datasets", json={"name": "Support QA"}).json()
        alice.post(f"{self.base}/datasets/{dataset['id']}/import",
                   json={"format": "json", "content": __import__("json").dumps(CASES)})
        self.dataset = dataset
        self.version = alice.post(f"{self.base}/datasets/{dataset['id']}/versions", json={}).json()

    def create_run(self, evaluators: list[dict] | None = None) -> dict[str, Any]:
        response = self.alice.post(f"{self.base}/eval-runs", json={
            "dataset_version_id": self.version["id"], "name": "nightly", "evaluators": evaluators or EVALUATORS})
        assert response.status_code == 201, response.text
        return response.json()

    def executor_cases(self, run: dict[str, Any]) -> dict[str, str]:
        body = self.client.get(f"/api/v1/sdk/eval-runs/{run['id']}", headers=bearer(self.key)).json()
        return {c["key"]: c["case_id"] for c in body["cases"]}

    def trace(self, sources: list[str], error: bool = False) -> str:
        trace_id, root = uuid.uuid4(), uuid.uuid4()
        spans = [
            {"id": str(root), "trace_id": str(trace_id), "name": "agent", "kind": "agent", "started_at": T0.isoformat(),
             "ended_at": (T0 + timedelta(milliseconds=50)).isoformat()},
            {"id": str(uuid.uuid4()), "trace_id": str(trace_id), "parent_span_id": str(root), "name": "kb", "kind": "retrieval",
             "started_at": T0.isoformat(), "ended_at": (T0 + timedelta(milliseconds=5)).isoformat(),
             "attributes": {"documents": [{"source": s, "score": 0.9} for s in sources]}},
        ]
        if error:
            spans.append({"id": str(uuid.uuid4()), "trace_id": str(trace_id), "parent_span_id": str(root), "name": "tool",
                          "kind": "tool", "status": "error", "started_at": T0.isoformat(), "ended_at": T0.isoformat(),
                          "error": {"type": "TimeoutError", "message": "slow"}})
        batch = {"traces": [{"id": str(trace_id), "name": "eval-case", "started_at": T0.isoformat(),
                             "ended_at": (T0 + timedelta(milliseconds=50)).isoformat()}], "spans": spans}
        assert self.client.post("/api/v1/ingest/batch", json=batch, headers=bearer(self.key)).status_code == 202
        return str(trace_id)

    def submit(self, run: dict[str, Any], results: list[dict[str, Any]]) -> Any:
        return self.client.post(f"/api/v1/sdk/eval-runs/{run['id']}/results", json={"results": results}, headers=bearer(self.key))

    def finish(self, run: dict[str, Any]) -> Any:
        return self.client.post(f"/api/v1/sdk/eval-runs/{run['id']}/finish", headers=bearer(self.key))

    def results(self, run: dict[str, Any], **params: str) -> list[dict[str, Any]]:
        return self.alice.get(f"{self.base}/eval-runs/{run['id']}/results", params=params).json()


@pytest.fixture
def env(alice: ApiClient, client: ApiClient) -> Env:
    return Env(alice, client)


def test_full_run_scores_cases_links_real_traces_only_and_reports_pass_rate(env: Env) -> None:
    run = env.create_run()
    assert run["status"] == "pending" and run["total_cases"] == 3 and run["pass_rate"] is None
    executor = env.client.get(f"/api/v1/sdk/eval-runs/{run['id']}", headers=bearer(env.key)).json()
    assert [c["key"] for c in executor["cases"]] == ["shipping", "refunds", "warranty"]
    assert "expected_output" not in str(executor) and "kb/shipping.md" not in str(executor)  # answers do not leak

    ids = env.executor_cases(run)
    shipping_trace = env.trace(["kb/shipping.md"])
    refunds_trace = env.trace(["kb/warranty.md"], error=True)  # retrieved the wrong document
    fabricated = str(uuid.uuid4())  # never ingested
    ack = env.submit(run, [
        {"case_id": ids["shipping"], "output": {"answer": "Your order is SHIPPED!"}, "trace_id": shipping_trace, "latency_ms": 41},
        {"case_id": ids["refunds"], "output": {"answer": "Refunds take 5 days."}, "trace_id": refunds_trace},
        {"case_id": ids["warranty"], "output": {"answer": "Two years."}, "trace_id": fabricated},
    ]).json()
    assert ack == {"accepted": 3, "status": "running", "received_cases": 3, "total_cases": 3}

    finished = env.finish(run).json()
    assert finished["status"] == "completed" and finished["finished_at"] is not None
    summary = finished["summary"]
    assert (summary["passed"], summary["failed"], summary["missing_cases"]) == (1, 2, 0)
    assert finished["pass_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # The warranty case has no expected sources, so source_match does not apply to it.
    assert summary["evaluators"]["source_match"] == {"passed": 1, "failed": 1, "not_applicable": 1, "pass_rate": 0.5}

    results = {r["key"]: r for r in env.results(run)}
    assert results["shipping"]["state"] == "passed" and results["shipping"]["trace_id"] == shipping_trace
    refunds = results["refunds"]
    assert refunds["state"] == "failed" and refunds["trace_id"] == refunds_trace
    source = next(e for e in refunds["evaluations"] if e["type"] == "source_match")
    assert source["passed"] is False and source["actual"] == ["kb/warranty.md"] and "kb/returns.md" in source["explanation"]
    assert source["supporting_refs"][0]["trace_id"] == refunds_trace and source["supporting_refs"][0]["reason"] == "retrieval span"
    normalized = next(e for e in refunds["evaluations"] if e["type"] == "normalized_match")
    assert normalized["passed"] is True and {"trace_id": refunds_trace} in normalized["supporting_refs"]
    assert any(r.get("reason") == "error span" for r in normalized["supporting_refs"])
    warranty = results["warranty"]
    assert warranty["trace_id"] is None  # the submitted id does not exist: no link is fabricated
    assert next(e for e in warranty["evaluations"] if e["type"] == "source_match")["applicable"] is False
    assert [r["key"] for r in env.results(run, state="failed")] == ["refunds", "warranty"]


def test_missing_cases_make_the_run_incomplete_without_a_pass_rate(env: Env) -> None:
    run = env.create_run()
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": ids["shipping"], "output": {"answer": "Your order is shipped."}}])
    finished = env.finish(run).json()
    assert finished["status"] == "incomplete" and finished["pass_rate"] is None
    assert finished["summary"]["missing_cases"] == 2 and finished["summary"]["received_cases"] == 1
    states = {r["key"]: r["state"] for r in env.results(run)}
    assert states == {"shipping": "failed", "refunds": "missing", "warranty": "missing"}  # no trace → source_match fails


def test_a_run_finished_without_any_result_is_incomplete(env: Env) -> None:
    run = env.create_run()
    assert env.finish(run).json()["status"] == "incomplete"


def test_execution_errors_fail_the_case_with_an_explanation(env: Env) -> None:
    run = env.create_run([{"type": "normalized_match", "field": "answer"}])
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": i, "error": {"type": "TimeoutError", "message": "model timed out"}} for i in ids.values()])
    finished = env.finish(run).json()
    assert finished["status"] == "completed" and finished["pass_rate"] == 0 and finished["summary"]["execution_errors"] == 3
    first = env.results(run)[0]
    assert first["evaluations"][0]["explanation"] == "The task raised TimeoutError: model timed out"


def test_scoring_exception_fails_the_run_and_persists_no_scores(env: Env, monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    run = env.create_run()
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": i, "output": {"answer": "x"}} for i in ids.values()])

    def broken(*_args: Any) -> dict:
        raise RuntimeError("evaluator bug")

    monkeypatch.setattr(ev, "evaluate", broken)
    finished = env.finish(run).json()
    assert finished["status"] == "failed" and finished["pass_rate"] is None
    assert "RuntimeError: evaluator bug" in finished["failure_reason"]
    assert all(r.scored_at is None and r.passed is None for r in db.scalars(select(EvaluationResult)))


def test_results_are_rejected_after_the_run_leaves_accepting_states(env: Env) -> None:
    run = env.create_run()
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": ids["shipping"], "output": {"answer": "a"}}])
    env.submit(run, [{"case_id": ids["shipping"], "output": {"answer": "Your order is shipped."}}])  # replaces
    assert env.finish(run).json()["status"] == "incomplete"
    late = env.submit(run, [{"case_id": ids["refunds"], "output": {"answer": "late"}}])
    assert late.status_code == 409 and late.json()["error"]["code"] == "run_not_accepting_results"
    again = env.finish(run)
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_not_finishable"
    assert env.results(run)[0]["output"] == {"answer": "Your order is shipped."}


def test_unknown_case_ids_are_rejected(env: Env) -> None:
    run = env.create_run()
    response = env.submit(run, [{"case_id": str(uuid.uuid4()), "output": 1}])
    assert response.status_code == 422 and response.json()["error"]["code"] == "unknown_cases"
    assert env.alice.get(f"{env.base}/eval-runs/{run['id']}").json()["status"] == "pending"


def test_abort_and_rerun(env: Env) -> None:
    run = env.create_run()
    aborted = env.alice.post(f"{env.base}/eval-runs/{run['id']}/abort")
    assert aborted.json()["status"] == "failed" and "Aborted by alice@example.com" in aborted.json()["failure_reason"]
    assert env.alice.post(f"{env.base}/eval-runs/{run['id']}/abort").status_code == 409
    rerun = env.alice.post(f"{env.base}/eval-runs/{run['id']}/rerun").json()
    assert rerun["status"] == "pending" and rerun["id"] != run["id"]
    assert rerun["dataset_version_id"] == run["dataset_version_id"] and rerun["evaluators"] == run["evaluators"]
    assert env.alice.get(f"{env.base}/eval-runs/{run['id']}").json()["status"] == "failed"


def test_invalid_evaluator_configuration_is_rejected(env: Env) -> None:
    response = env.alice.post(f"{env.base}/eval-runs", json={
        "dataset_version_id": env.version["id"], "name": "x", "evaluators": [{"type": "llm_judge"}]})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_evaluators"


def test_concurrent_finish_scores_exactly_once(env: Env, make_client) -> None:
    run = env.create_run()
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": i, "output": {"answer": "x"}} for i in ids.values()])
    clients = [make_client() for _ in range(6)]
    barrier = threading.Barrier(len(clients))
    codes: list[int] = []

    def finish(c: ApiClient) -> None:
        barrier.wait()
        codes.append(c.post(f"/api/v1/sdk/eval-runs/{run['id']}/finish", headers=bearer(env.key)).status_code)

    threads = [threading.Thread(target=finish, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(codes) == [200] + [409] * 5
    assert env.alice.get(f"{env.base}/eval-runs/{run['id']}").json()["status"] == "completed"


def test_database_enforces_state_machine_and_terminal_immutability(env: Env, db: Session) -> None:
    pending = env.create_run()
    with pytest.raises(DBAPIError, match="invalid evaluation run transition pending -> completed"):
        db.execute(text("UPDATE evaluation_runs SET status = 'completed', finished_at = now() WHERE id = :i"), {"i": pending["id"]})
    db.rollback()
    with pytest.raises(DBAPIError, match="configuration is immutable"):
        db.execute(text("UPDATE evaluation_runs SET evaluators = '[]' WHERE id = :i"), {"i": pending["id"]})
    db.rollback()

    done = env.create_run()
    ids = env.executor_cases(done)
    env.submit(done, [{"case_id": ids["shipping"], "output": {"answer": "x"}}])
    env.finish(done)
    case_id = ids["refunds"]
    for statement, params in (
        ("UPDATE evaluation_runs SET name = 'x' WHERE id = :r", {}),
        ("INSERT INTO evaluation_results (id, run_id, case_id) VALUES (gen_random_uuid(), :r, :c)", {"c": case_id}),
        ("UPDATE evaluation_results SET passed = true WHERE run_id = :r", {}),
        ("DELETE FROM evaluation_results WHERE run_id = :r", {}),
    ):
        with pytest.raises(DBAPIError, match="immutable|only be submitted"):
            db.execute(text(statement), {"r": done["id"], **params})
        db.rollback()
    assert db.get(EvaluationRun, uuid.UUID(done["id"])).status == "incomplete"


def test_runs_are_isolated_between_projects_and_users(env: Env, bob: ApiClient, client: ApiClient) -> None:
    run = env.create_run()
    other_project = env.alice.create_project("Other", slug="other")
    other_key = env.alice.create_api_key(other_project["id"])["key"]
    for response in (
        client.get(f"/api/v1/sdk/eval-runs/{run['id']}", headers=bearer(other_key)),
        client.post(f"/api/v1/sdk/eval-runs/{run['id']}/results", json={"results": [{"case_id": str(uuid.uuid4())}]},
                    headers=bearer(other_key)),
        client.post(f"/api/v1/sdk/eval-runs/{run['id']}/finish", headers=bearer(other_key)),
        bob.get(f"{env.base}/eval-runs/{run['id']}"),
        bob.get(f"{env.base}/eval-runs/{run['id']}/results"),
        bob.post(f"{env.base}/eval-runs/{run['id']}/finish"),
        env.alice.get(f"/api/v1/projects/{other_project['id']}/eval-runs/{run['id']}"),
    ):
        assert response.status_code == 404
    # A dataset version of another project cannot be used to create a run.
    response = env.alice.post(f"/api/v1/projects/{other_project['id']}/eval-runs", json={
        "dataset_version_id": env.version["id"], "name": "x", "evaluators": EVALUATORS})
    assert response.status_code == 404 and response.json()["error"]["code"] == "dataset_version_not_found"
    assert env.alice.get(f"{env.base}/eval-runs/{run['id']}").json()["status"] == "pending"


def test_sdk_can_create_a_run_by_dataset_name_and_latest_version(env: Env) -> None:
    response = env.client.post("/api/v1/sdk/eval-runs", json={"dataset": "Support QA", "name": "ci", "evaluators": EVALUATORS},
                               headers=bearer(env.key))
    assert response.status_code == 201 and response.json()["dataset_version"] == 1
    missing = env.client.post("/api/v1/sdk/eval-runs", json={"dataset": "Nope", "name": "ci", "evaluators": EVALUATORS},
                              headers=bearer(env.key))
    assert missing.status_code == 404


def test_deleting_the_project_or_dataset_removes_terminal_runs(env: Env, db: Session) -> None:
    run = env.create_run()
    env.finish(run)
    assert env.alice.delete(f"{env.base}/datasets/{env.dataset['id']}").status_code == 204
    assert db.get(EvaluationRun, uuid.UUID(run["id"])) is None
