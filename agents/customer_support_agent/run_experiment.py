"""Execute a controlled baseline vs. candidate experiment on Customer Support Agent.

Both arms run the *identical* dataset cases, including every injected failure (``override_mode``). Earlier
versions of this script rewrote ``context_selection_failure`` to ``normal`` in the candidate arm only, which made
injected failures look "fixed"; that rewrite is removed (M10). The task never modifies case inputs.

usage: python -m agents.customer_support_agent.run_experiment [--source-run NAME] [--candidate multi_intent|intent_aligned]
"""

import argparse
import os
import sys
import uuid
from typing import Any

sys.path.insert(0, os.path.abspath("backend"))

import traceforge

from agents.customer_support_agent.agent import execute_agent_task
from agents.customer_support_agent.config import get_settings
from app.core.config import get_settings as get_backend_settings
from app.db.session import make_engine, make_sessionmaker
from app.models import EvaluationRun
from app.services import experiments as exp_svc
from sqlalchemy import select

PROJECT_ID = uuid.UUID("9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e")
USER_ID = uuid.UUID("803ed58e-90ca-4099-b2f0-10b168d9de06")

BASELINE_CONFIG = {"retriever_strategy": "keyword", "context_strategy": "best_rank", "mode": "baseline"}
CANDIDATES = {
    "multi_intent": {
        "name": "Multi-Intent Retrieval",
        "description": "Tests per-intent retrieval and multi-document synthesis against single-intent keyword retrieval. "
                       "Both arms receive identical cases and injected failures.",
        "config": {"retriever_strategy": "multi_intent", "context_strategy": "multi_intent", "mode": "candidate"},
    },
    "intent_aligned": {
        "name": "Context-Selection Alignment Optimization",
        "description": "Intent-weighted retrieval with intent-aligned context selection (the M9 candidate), re-run with "
                       "identical injected failures in both arms.",
        "config": {"retriever_strategy": "intent_weighted", "context_strategy": "intent_aligned", "mode": "candidate"},
    },
}


def experiment_task(case_input: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """The same task for both arms: only ``config`` differs. Case inputs are passed through untouched."""
    return execute_agent_task(dict(case_input), config=config)


def run_controlled_experiment(source_run_name: str = "customer-support-baseline-v3", candidate: str = "multi_intent") -> dict[str, Any]:
    agent_settings = get_settings()
    backend_settings = get_backend_settings()
    engine = make_engine(backend_settings.dev_database_url.get_secret_value())
    Session = make_sessionmaker(engine)
    spec = CANDIDATES[candidate]

    with Session() as session:
        source_run = session.scalar(
            select(EvaluationRun)
            .where(EvaluationRun.project_id == PROJECT_ID, EvaluationRun.name == source_run_name, EvaluationRun.status == "completed")
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        )
        if not source_run:
            raise RuntimeError(f"No completed evaluation run named {source_run_name!r} found.")
        print(f"Creating experiment for source run {source_run.id} ({source_run.name!r})")
        experiment = exp_svc.create_experiment(
            session,
            project_id=PROJECT_ID,
            source_run_id=source_run.id,
            name=spec["name"],
            description=spec["description"],
            baseline_config=BASELINE_CONFIG,
            candidate_config=spec["config"],
            hypothesis_id=None,
            user_id=USER_ID,
            max_cases=backend_settings.experiment_max_cases,
        )
        session.commit()
        exp_id = experiment.id
        print(f"Created experiment {exp_id} (status {experiment.status})")

    client = traceforge.init(api_key=agent_settings.api_key, endpoint=agent_settings.endpoint)
    if not client.enabled:
        raise RuntimeError("Traceforge SDK client failed to initialize.")

    print("Running both arms via traceforge.run_experiment() ...")
    completed = traceforge.run_experiment(exp_id, experiment_task)

    comparison = completed.get("comparison", {})
    rel = comparison.get("reliability", {})
    print("\n--- Experiment results ---")
    print(f"Experiment ID: {completed.get('id')}  status: {completed.get('status')}")
    print(f"Pass rate: baseline {comparison.get('arms', {}).get('baseline', {}).get('pass_rate')} -> "
          f"candidate {comparison.get('arms', {}).get('candidate', {}).get('pass_rate')} (delta {comparison.get('pass_rate_delta')})")
    for group in ("fixed", "genuinely_fixed", "regressed"):
        print(f"{group}: {rel.get('cases', {}).get(group)}")
    print(f"Unchanged: {len(rel.get('cases', {}).get('unchanged_pass', []))} pass, {len(rel.get('cases', {}).get('unchanged_fail', []))} fail")
    print(f"Positive controls: {rel.get('positive_controls')}")
    print(f"Negative controls: {rel.get('negative_controls')}")
    for flag in rel.get("flags", []):
        print(f"FLAG {flag['code']} ({'blocking' if flag['blocking'] else 'info'}): {flag['message']} {flag['cases']}")
    print(f"Recommendation: {rel.get('recommendation')} (human approval required: {rel.get('requires_human_approval')})")
    print(f"Suggested outcome: {comparison.get('suggested_outcome')} - {comparison.get('suggested_outcome_reason')}")
    return completed


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", default="customer-support-baseline-v3")
    parser.add_argument("--candidate", default="multi_intent", choices=sorted(CANDIDATES))
    args = parser.parse_args()
    run_controlled_experiment(args.source_run, args.candidate)
