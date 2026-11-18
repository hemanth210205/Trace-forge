"""Sync and evaluate the pre-registered holdout (see README.md). Refuses to run if the cases changed.

    python -m agents.customer_support_agent.holdout.run_holdout sync       # create "Customer Support Holdout" v1
    python -m agents.customer_support_agent.holdout.run_holdout evaluate   # baseline evaluation run on it

The dataset is separate from "Customer Support QA Dataset"; its versions 1-3 are never touched.
"""

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.abspath("backend"))

HERE = Path(__file__).resolve().parent
CASES_PATH = HERE / "holdout_cases.json"
CANONICAL_SHA256 = "a71f9ef77b639fc3ca126f7af9be0007bff1d44ec9d7a41bbcbb8919d0c5a72d"  # pre-registered, README.md
PROJECT_ID = uuid.UUID("9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e")
USER_ID = uuid.UUID("803ed58e-90ca-4099-b2f0-10b168d9de06")
DATASET_NAME = "Customer Support Holdout"
RUN_NAME = "customer-support-holdout-baseline"
EVALUATORS = [
    {"type": "contains", "field": "answer", "name": "key_facts"},
    {"type": "source_match", "name": "source_retrieval_match"},
    {"type": "required_fields", "fields": ["answer"], "name": "required_answer_field"},
]


def verified_cases() -> str:
    raw = CASES_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha256(json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if digest != CANONICAL_SHA256:
        raise SystemExit(f"Holdout cases changed since pre-registration (sha256 {digest}); refusing to run.")
    return raw


def sync() -> None:
    from sqlalchemy import select

    from app.core.config import get_settings
    from app.db.session import make_engine, make_sessionmaker
    from app.models import Dataset
    from app.services import datasets as svc

    raw = verified_cases()
    Session = make_sessionmaker(make_engine(get_settings().dev_database_url.get_secret_value()))
    with Session() as session:
        dataset = session.scalar(select(Dataset).where(Dataset.project_id == PROJECT_ID, Dataset.name == DATASET_NAME))
        if dataset is not None and svc.latest_version(session, dataset.id) is not None:
            v = svc.latest_version(session, dataset.id)
            print(f"Holdout dataset already published: {dataset.id} v{v.version} ({v.case_count} cases); nothing changed.")
            return
        if dataset is None:
            dataset = Dataset(project_id=PROJECT_ID, name=DATASET_NAME, created_by=USER_ID,
                              description="M10 pre-registered holdout (same author, not independently validated). Not for tuning.")
            session.add(dataset)
            session.flush()
        count = svc.import_cases(session, dataset, svc.parse_import("json", raw), mode="replace")
        version = svc.publish(session, dataset, notes=f"Pre-registered holdout, canonical sha256 {CANONICAL_SHA256}", user_id=USER_ID)
        session.commit()
        print(f"Created {DATASET_NAME} {dataset.id}: imported {count} cases, published v{version.version} ({version.case_count} cases).")


def evaluate() -> None:
    import traceforge

    from agents.customer_support_agent.agent import execute_agent_task
    from agents.customer_support_agent.config import get_settings as agent_settings

    verified_cases()
    settings = agent_settings()
    client = traceforge.init(api_key=settings.api_key, endpoint=settings.endpoint)
    if not client.enabled:
        raise SystemExit("Traceforge client could not be enabled.")
    run = traceforge.create_evaluation_run(dataset=DATASET_NAME, name=RUN_NAME, version=1, evaluators=EVALUATORS)

    def task(case_input: dict[str, Any]) -> dict[str, Any]:
        return execute_agent_task(dict(case_input), config={"retriever_strategy": "keyword", "context_strategy": "best_rank"})

    summary = traceforge.evaluate(run["id"], task, trace_name="customer-support-holdout-case")
    print(f"Run {run['id']}: status {summary.get('status')}, cases {summary.get('case_count')}")
    print(json.dumps(summary.get("scores"), indent=1))


if __name__ == "__main__":
    {"sync": sync, "evaluate": evaluate}[sys.argv[1] if len(sys.argv) > 1 else "sync"]()
