"""M5 acceptance (API level): dataset → version → run → separate-process SDK execution → per-case results
with links that resolve to the real traces and spans."""

import json
import os
import subprocess
import sys

from fastapi import FastAPI

from app.serve import SERVER_OPTIONS
from tests.conftest import BACKEND_DIR, ApiClient
from tests.test_server_runtime import serve_in_thread

REPO = BACKEND_DIR.parent
EXAMPLE = REPO / "examples" / "sample_agent"
EVALUATORS = [
    {"type": "normalized_match", "field": "answer"},
    {"type": "source_match"},
    {"type": "required_fields", "fields": ["answer"]},
]


def test_sample_agent_executes_an_evaluation_run_end_to_end(app: FastAPI, alice: ApiClient) -> None:
    project = alice.create_project("Support agent", slug="support")
    key = alice.create_api_key(project["id"], "evaluator")["key"]
    base = f"/api/v1/projects/{project['id']}"
    dataset = alice.post(f"{base}/datasets", json={"name": "Support QA"}).json()
    content = (EXAMPLE / "support_qa.jsonl").read_text(encoding="utf-8")
    assert alice.post(f"{base}/datasets/{dataset['id']}/import", json={"format": "jsonl", "content": content}).json()["imported"] == 4
    version = alice.post(f"{base}/datasets/{dataset['id']}/versions", json={"notes": "baseline"}).json()
    run = alice.post(f"{base}/eval-runs", json={"dataset_version_id": version["id"], "name": "nightly", "evaluators": EVALUATORS}).json()

    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        result = subprocess.run(
            [sys.executable, str(EXAMPLE / "evaluate.py"), "--run", run["id"], "--endpoint", base_url],
            env={**os.environ, "PYTHONPATH": str(REPO / "sdk" / "python" / "src"), "TRACEFORGE_API_KEY": key},
            capture_output=True, text=True, timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    reported = json.loads(result.stdout)
    assert reported["status"] == "completed" and reported["pass_rate"] == 0.5

    finished = alice.get(f"{base}/eval-runs/{run['id']}").json()
    assert finished["status"] == "completed" and finished["summary"]["passed"] == 2 and finished["summary"]["failed"] == 2
    results = {r["key"]: r for r in alice.get(f"{base}/eval-runs/{run['id']}/results").json()}
    assert {k: r["state"] for k, r in results.items()} == {
        "shipping-a100": "failed", "refund-policy": "passed", "unknown-order": "passed", "warranty-accidental-damage": "failed"}

    # Retrieval miss: the answer is right, but the agent's keyword search did not retrieve kb/shipping.md.
    shipping = results["shipping-a100"]
    by_type = {e["type"]: e for e in shipping["evaluations"]}
    assert by_type["normalized_match"]["passed"] is True
    assert by_type["source_match"]["passed"] is False and by_type["source_match"]["actual"] == []
    assert "Not retrieved: kb/shipping.md" in by_type["source_match"]["explanation"]

    failed = results["warranty-accidental-damage"]
    normalized = next(e for e in failed["evaluations"] if e["type"] == "normalized_match")
    assert normalized["passed"] is False and normalized["actual"].startswith("According to our policy: Items can be returned")
    sources = next(e for e in failed["evaluations"] if e["type"] == "source_match")
    assert sources["passed"] is True  # it did retrieve the warranty document, but answered from the returns one

    # Every result links to its real trace, and the supporting span ids exist in that trace.
    for key_, r in results.items():
        assert r["trace_id"], key_
        trace = alice.get(f"{base}/traces/{r['trace_id']}").json()
        assert trace["input"] == r["input"] and "evaluation" in trace["tags"]
        span_ids = {s["id"] for s in trace["spans"]}
        for evaluation in r["evaluations"]:
            for ref in evaluation["supporting_refs"]:
                assert ref["trace_id"] == r["trace_id"] and ref.get("span_id", next(iter(span_ids))) in span_ids
    retrieval_ref = sources["supporting_refs"][0]
    span = next(s for s in alice.get(f"{base}/traces/{failed['trace_id']}").json()["spans"] if s["id"] == retrieval_ref["span_id"])
    assert span["kind"] == "retrieval" and "kb/warranty.md" in json.dumps(span["attributes"])
