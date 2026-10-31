"""M7 acceptance (API level): a real experiment with the sample agent, executed in a separate process.

Source run (default config) → diagnosis → experiment testing the shipping retrieval hypothesis (baseline
retriever "keyword" vs candidate "stemmed") → both arms replayed on the same dataset version by
examples/sample_agent/experiment.py → comparison → hypothesis validation → human decision.
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


def run_script(base_url: str, key: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args, "--endpoint", base_url],
                          env={**os.environ, "PYTHONPATH": str(REPO / "sdk" / "python" / "src"), "TRACEFORGE_API_KEY": key},
                          capture_output=True, text=True, timeout=120)


def test_experiment_replays_both_configurations_and_compares_them(app: FastAPI, alice: ApiClient) -> None:
    project = alice.create_project("Support agent", slug="support")
    key = alice.create_api_key(project["id"], "runner")["key"]
    base = f"/api/v1/projects/{project['id']}"
    dataset = alice.post(f"{base}/datasets", json={"name": "Support QA"}).json()
    alice.post(f"{base}/datasets/{dataset['id']}/import", json={"format": "jsonl", "content": (EXAMPLE / "support_qa.jsonl").read_text(encoding="utf-8")})
    version = alice.post(f"{base}/datasets/{dataset['id']}/versions", json={}).json()
    source = alice.post(f"{base}/eval-runs", json={"dataset_version_id": version["id"], "name": "baseline", "evaluators": EVALUATORS}).json()

    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        assert run_script(base_url, key, str(EXAMPLE / "evaluate.py"), "--run", source["id"]).returncode == 0
        diagnosis_id = alice.post(f"{base}/eval-runs/{source['id']}/diagnoses").json()["id"]
        diagnosis = alice.get(f"{base}/diagnoses/{diagnosis_id}").json()
        shipping = next(c for c in diagnosis["cases"] if c["case_key"] == "shipping-a100")
        retrieval = next(h for h in shipping["hypotheses"] if h["category"] == "retrieval_failure")

        created = alice.post(f"{base}/experiments", json={
            "source_run_id": source["id"], "name": "stemmed retriever", "hypothesis_id": retrieval["id"],
            "baseline_config": {"retriever": "keyword"}, "candidate_config": {"retriever": "stemmed"},
        })
        assert created.status_code == 201, created.text
        experiment = created.json()
        result = run_script(base_url, key, str(EXAMPLE / "experiment.py"), "--experiment", experiment["id"])
    assert result.returncode == 0, result.stdout + result.stderr

    done = alice.get(f"{base}/experiments/{experiment['id']}").json()
    assert done["status"] == "completed"
    assert done["baseline"]["run_status"] == done["candidate"]["run_status"] == "completed"
    comparison = done["comparison"]
    # Baseline reproduces the source run exactly; stemming fixes the retrieval miss and nothing regresses.
    assert comparison["arms"]["baseline"]["pass_rate"] == 0.5
    assert comparison["cases"]["fixed"] == ["shipping-a100"]
    assert comparison["cases"]["regressed"] == []
    assert comparison["cases"]["unchanged_fail"] == ["warranty-accidental-damage"]
    assert comparison["pass_rate_delta"] == 0.25 and comparison["suggested_outcome"] == "candidate_improves"
    assert comparison["arms"]["candidate"]["retrieval"]["mean_recall"] > comparison["arms"]["baseline"]["retrieval"]["mean_recall"]
    for measure in ("latency_ms", "tokens", "cost_usd"):
        assert comparison["measurements"][measure]["paired_cases"] == 4, measure

    cases = {c["case_key"]: c for c in alice.get(f"{base}/experiments/{experiment['id']}/cases").json()}
    shipping_case = cases["shipping-a100"]
    assert {"evaluator": "source_match", "change": "fixed"} in shipping_case["evaluator_changes"]
    candidate_trace = alice.get(f"{base}/traces/{shipping_case['candidate']['trace_id']}").json()
    assert {"experiment", "candidate"} <= set(candidate_trace["tags"])
    agent_span = next(s for s in candidate_trace["spans"] if s["kind"] == "agent")
    assert agent_span["metadata"]["config"]["retriever"] == "stemmed"
    retrieval_span = next(s for s in candidate_trace["spans"] if s["kind"] == "retrieval")
    assert any(d["source"] == "kb/shipping.md" for d in retrieval_span["attributes"]["documents"])

    # The diagnosis's recommendation is context; validation needs the experiment's evidence.
    assert done["hypothesis"]["validation_status"] == "unvalidated" and done["hypothesis_validation"]["eligible"] is True
    validated = alice.post(f"{base}/experiments/{experiment['id']}/validate-hypothesis").json()
    assert validated["hypothesis"]["validated_by"] == f"experiment:{experiment['id']}"
    refreshed = alice.get(f"{base}/diagnoses/{diagnosis_id}").json()
    assert next(h for c in refreshed["cases"] for h in c["hypotheses"] if h["id"] == retrieval["id"])["validation_status"] == "validated"

    decided = alice.post(f"{base}/experiments/{experiment['id']}/decisions",
                         json={"decision": "approve", "rationale": "Stemming fixes the shipping retrieval miss with no regressions."}).json()
    assert [d["decision"] for d in decided["decisions"]] == ["approve"]
    printed: dict[str, Any] = json.loads(result.stdout)
    assert printed["status"] == "completed" and printed["suggested_outcome"] == "candidate_improves"
