"""M3 acceptance: the separate sample agent, using the real SDK over HTTP, sends traces that a real uvicorn
server (backed by traceforge_test) stores and the console API returns."""

import json
import os
import subprocess
import sys
from typing import Any

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.serve import SERVER_OPTIONS
from tests.conftest import BACKEND_DIR, ApiClient
from tests.test_server_runtime import serve_in_thread

REPO = BACKEND_DIR.parent
AGENT = REPO / "examples" / "sample_agent" / "agent.py"
SDK_SRC = REPO / "sdk" / "python" / "src"


def run_agent(base_url: str, api_key: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SDK_SRC), "TRACEFORGE_API_KEY": api_key}
    return subprocess.run(
        [sys.executable, str(AGENT), "--endpoint", base_url], env=env, capture_output=True, text=True, timeout=60
    )


def test_sample_agent_traces_reach_the_console(app: FastAPI, alice: ApiClient, db: Session) -> None:
    project = alice.create_project("Support agent", slug="support")
    api_key = alice.create_api_key(project["id"], "sample-agent")["key"]

    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        result = run_agent(base_url, api_key)
    assert result.returncode == 0, result.stdout + result.stderr
    stats = json.loads(result.stdout.rsplit("telemetry: ", 1)[1])
    # 3 traces; spans: agent + retrieval + LLM each, plus the order tool for the 2 questions naming an order.
    assert stats["sent_items"] == 3 + 4 + 3 + 4 and stats["dropped_items"] == 0

    listing = alice.get(f"/api/v1/projects/{project['id']}/traces").json()["items"]
    assert len(listing) == 3 and {t["name"] for t in listing} == {"support-request"}
    assert all(t["has_trace_record"] and t["status"] == "ok" for t in listing)

    details = [alice.get(f"/api/v1/projects/{project['id']}/traces/{t['id']}").json() for t in listing]
    by_question = {d["input"]["question"]: d for d in details}
    assert {q: d["span_count"] for q, d in by_question.items()} == {
        "Where is my order A100?": 4,
        "How do refunds work for a return?": 3,
        "What is the status of order Z999?": 4,
    }

    shipped = by_question["Where is my order A100?"]
    spans = {s["name"]: s for s in shipped["spans"]}
    assert set(spans) == {"support-agent", "kb-search", "order-lookup", "compose-answer"}
    agent = spans["support-agent"]
    assert agent["kind"] == "agent" and agent["parent_span_id"] is None
    for child in ("kb-search", "order-lookup", "compose-answer"):
        assert spans[child]["parent_span_id"] == agent["id"]
    assert spans["order-lookup"]["kind"] == "tool" and spans["order-lookup"]["output"]["status"] == "shipped"
    assert spans["order-lookup"]["attributes"]["tool"] == {"name": "order-lookup", "arguments": {"order_id": "A100"}}
    llm = spans["compose-answer"]
    assert llm["kind"] == "llm" and llm["model"] == "sample-template-llm"
    assert llm["prompt_tokens"] > 0 and llm["total_tokens"] == llm["prompt_tokens"] + llm["completion_tokens"]
    assert shipped["total_tokens"] == llm["total_tokens"] and float(shipped["total_cost_usd"]) > 0
    assert shipped["output"] == {"answer": "Your order is shipped with ParcelCo."}
    assert all(s["duration_ms"] is not None and s["duration_ms"] >= 0 for s in shipped["spans"])

    refund = by_question["How do refunds work for a return?"]
    docs = {s["name"]: s for s in refund["spans"]}["kb-search"]["attributes"]["documents"]
    assert {d["source"] for d in docs} >= {"kb/returns.md"} and all("score" in d for d in docs)

    missing = by_question["What is the status of order Z999?"]
    tool = {s["name"]: s for s in missing["spans"]}["order-lookup"]
    assert tool["status"] == "error" and tool["error"]["type"] == "LookupError"
    assert "Z999" in tool["error"]["message"] and missing["error_count"] == 1
    assert missing["status"] == "ok"  # the agent recovered; the failure is visible on the tool span

    # Secrets in the agent's input were redacted by the SDK before they left the process.
    stored = db.execute(text("SELECT input::text FROM traces UNION ALL SELECT input::text FROM spans")).scalars().all()
    blob = " ".join(s for s in stored if s)
    assert "fake-key-123" not in blob and "sk-" + "x" * 24 not in blob
    assert shipped["input"]["customer"]["api_key"] == "[REDACTED]"


def test_sample_agent_reports_failure_with_an_invalid_key(app: FastAPI) -> None:
    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        result = run_agent(base_url, "tf_deadbeef_" + "x" * 43)
    assert result.returncode == 1  # the agent itself still completed; telemetry was rejected
    assert "Your order is shipped" in result.stdout
    stats: dict[str, Any] = json.loads(result.stdout.rsplit("telemetry: ", 1)[1])
    assert stats["sent_items"] == 0 and stats["failed_batches"] >= 1
