"""Run end-to-end evaluation, diagnosis, and experiment on Customer Support Agent."""

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

import traceforge

from agents.customer_support_agent.agent import execute_agent_task
from agents.customer_support_agent.config import get_settings


def run_eval():
    settings = get_settings()
    client = traceforge.init(api_key=settings.api_key, endpoint=settings.endpoint)
    if not client.enabled:
        raise RuntimeError("Traceforge client could not be enabled.")

    evaluators = [
        {"type": "normalized_match", "field": "answer", "name": "normalized_answer_match"},
        {"type": "source_match", "name": "source_retrieval_match"},
        {"type": "required_fields", "fields": ["answer"], "name": "required_answer_field"},
    ]

    print("Creating evaluation run on dataset 'Customer Support QA Dataset' (v3)...")
    run_record = traceforge.create_evaluation_run(
        dataset="Customer Support QA Dataset",
        name="customer-support-baseline-v3",
        version=3,
        evaluators=evaluators,
    )
    run_id = run_record["id"]
    print(f"Created Evaluation Run ID: {run_id}")

    def task(case_input: dict[str, Any]) -> dict[str, Any]:
        return execute_agent_task(
            case_input,
            config={"retriever_strategy": "keyword", "context_strategy": "best_rank"},
        )

    print("Executing all 50 test cases against CustomerSupportAgent...")
    summary = traceforge.evaluate(run_id, task, trace_name="customer-support-eval-case")
    print("\n--- Evaluation Run Summary ---")
    print(f"Status: {summary.get('status')}")
    print(f"Cases Evaluated: {summary.get('case_count')}")
    print(f"Scores: {json.dumps(summary.get('scores'), indent=2)}")

    return run_id, summary


if __name__ == "__main__":
    run_eval()
