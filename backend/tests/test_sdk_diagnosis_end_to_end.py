"""M6 acceptance (API level): the sample agent's real evaluation run is diagnosed from its persisted evidence.

Expected behaviour, derived from the traces (not hard-coded anywhere in the diagnosis code):
- warranty-accidental-damage: the expected document was retrieved, the answer was built from another one
  → context selection is supported; grounding and retrieval explanations are contradicted.
- shipping-a100: the answer passed; the retriever returned nothing → retrieval failure is supported for the
  source check only, without claiming an incorrect answer.
- a run submitted without traces → insufficient evidence for every case.
"""

import json
import os
import subprocess
import sys
from typing import Any

from fastapi import FastAPI

from app.serve import SERVER_OPTIONS
from tests.conftest import BACKEND_DIR, ApiClient
from tests.test_server_runtime import serve_in_thread

REPO = BACKEND_DIR.parent
EXAMPLE = REPO / "examples" / "sample_agent"
EVALUATORS = [{"type": "normalized_match", "field": "answer"}, {"type": "source_match"}, {"type": "required_fields", "fields": ["answer"]}]


def hypotheses(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {h["category"]: h for h in case["hypotheses"]}


def test_sample_agent_failures_are_diagnosed_from_evidence(app: FastAPI, alice: ApiClient, client: ApiClient) -> None:
    project = alice.create_project("Support agent", slug="support")
    key = alice.create_api_key(project["id"], "evaluator")["key"]
    base = f"/api/v1/projects/{project['id']}"
    dataset = alice.post(f"{base}/datasets", json={"name": "Support QA"}).json()
    alice.post(f"{base}/datasets/{dataset['id']}/import",
               json={"format": "jsonl", "content": (EXAMPLE / "support_qa.jsonl").read_text(encoding="utf-8")})
    version = alice.post(f"{base}/datasets/{dataset['id']}/versions", json={}).json()
    run = alice.post(f"{base}/eval-runs", json={"dataset_version_id": version["id"], "name": "baseline", "evaluators": EVALUATORS}).json()

    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        result = subprocess.run(
            [sys.executable, str(EXAMPLE / "evaluate.py"), "--run", run["id"], "--endpoint", base_url],
            env={**os.environ, "PYTHONPATH": str(REPO / "sdk" / "python" / "src"), "TRACEFORGE_API_KEY": key},
            capture_output=True, text=True, timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "completed"

    started = alice.post(f"{base}/eval-runs/{run['id']}/diagnoses")
    assert started.status_code == 202
    diagnosis = alice.get(f"{base}/diagnoses/{started.json()['id']}").json()
    assert diagnosis["status"] == "completed", diagnosis["error"]
    cases = {c["case_key"]: c for c in diagnosis["cases"]}
    assert set(cases) == {"shipping-a100", "warranty-accidental-damage"}

    warranty = hypotheses(cases["warranty-accidental-damage"])
    selection = warranty["context_selection_failure"]
    assert selection["evidence_status"] == "supported" and selection["validation_status"] == "unvalidated"
    assert "kb/warranty.md at rank 2" in selection["explanation"] and "kb/returns.md" in selection["explanation"]
    assert warranty["grounding_failure"]["evidence_status"] == "contradicted"
    assert warranty.get("retrieval_failure", {"evidence_status": "absent"})["evidence_status"] in ("absent", "contradicted")
    assert cases["warranty-accidental-damage"]["hypotheses"][0]["category"] == "context_selection_failure"  # ranked first

    shipping = hypotheses(cases["shipping-a100"])
    assert shipping["retrieval_failure"]["evidence_status"] == "supported"
    assert "not for an incorrect answer" in shipping["retrieval_failure"]["explanation"]
    assert not {"context_selection_failure", "grounding_failure", "reasoning_failure"} & {
        c for c, h in shipping.items() if h["evidence_status"] == "supported"}

    # Each cited span opens in the Trace Explorer: it belongs to the case's trace in this project.
    for case in diagnosis["cases"]:
        trace = alice.get(f"{base}/traces/{case['trace_id']}").json()
        spans = {s["id"]: s for s in trace["spans"]}
        for h in case["hypotheses"]:
            assert set(h["evidence_span_ids"]) <= set(spans)
    retrieval_span = spans_of(alice, base, cases["warranty-accidental-damage"], selection["evidence_span_ids"])
    assert any(s["kind"] == "retrieval" for s in retrieval_span)

    # A run whose results were submitted without traces can only be diagnosed as "insufficient evidence".
    blind = alice.post(f"{base}/eval-runs", json={"dataset_version_id": version["id"], "name": "untraced", "evaluators": EVALUATORS}).json()
    headers = {"Authorization": f"Bearer {key}"}
    cases_to_run = client.get(f"/api/v1/sdk/eval-runs/{blind['id']}", headers=headers).json()["cases"]
    client.post(f"/api/v1/sdk/eval-runs/{blind['id']}/results", headers=headers,
                json={"results": [{"case_id": c["case_id"], "output": {"answer": "I don't know."}} for c in cases_to_run]})
    assert client.post(f"/api/v1/sdk/eval-runs/{blind['id']}/finish", headers=headers).json()["status"] == "completed"
    untraced_id = alice.post(f"{base}/eval-runs/{blind['id']}/diagnoses").json()["id"]
    untraced = alice.get(f"{base}/diagnoses/{untraced_id}").json()
    assert untraced["status"] == "completed" and len(untraced["cases"]) == 4
    for case in untraced["cases"]:
        assert [h["category"] for h in case["hypotheses"]] == ["insufficient_evidence"]
        assert case["hypotheses"][0]["evidence_status"] == "inconclusive" and case["hypotheses"][0]["evidence_span_ids"] == []
    assert "4 case(s) lack the evidence for any conclusion" in untraced["summary"]


def spans_of(alice: ApiClient, base: str, case: dict[str, Any], span_ids: list[str]) -> list[dict[str, Any]]:
    trace = alice.get(f"{base}/traces/{case['trace_id']}").json()
    return [s for s in trace["spans"] if s["id"] in span_ids]
