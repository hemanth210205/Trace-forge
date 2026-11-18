"""Execute failure diagnosis on the evaluation run."""

import os
import sys
import uuid
from collections import Counter

sys.path.insert(0, os.path.abspath("backend"))

from app.core.config import get_settings
from app.db.session import make_engine, make_sessionmaker
from app.diagnosis.providers import build_provider
from app.diagnosis.workflow import execute_diagnosis
from app.models import Diagnosis, DiagnosisHypothesis, EvaluationRun
from sqlalchemy import select

PROJECT_ID = uuid.UUID("9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e")
USER_ID = uuid.UUID("803ed58e-90ca-4099-b2f0-10b168d9de06")


def diagnose_latest_run():
    settings = get_settings()
    engine = make_engine(settings.dev_database_url.get_secret_value())
    Session = make_sessionmaker(engine)

    with Session() as session:
        # Get the latest scored evaluation run
        run = session.scalar(
            select(EvaluationRun)
            .where(EvaluationRun.project_id == PROJECT_ID, EvaluationRun.status == "completed")
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        )
        if not run:
            raise RuntimeError("No completed evaluation run found to diagnose.")

        print(f"Diagnosing Evaluation Run: {run.id} ('{run.name}')")
        print(f"Run Summary: {run.summary}")

        provider = build_provider(settings)
        print(f"Using Diagnosis Provider: {provider.name} ({provider.model})")

        diagnosis = Diagnosis(
            project_id=PROJECT_ID,
            run_id=run.id,
            retry_of=None,
            provider=provider.name,
            model=provider.model,
            created_by=USER_ID,
        )
        session.add(diagnosis)
        session.commit()
        session.refresh(diagnosis)
        diag_id = diagnosis.id
        print(f"Created Diagnosis record: {diag_id}")

    # Execute diagnosis synchronously
    print("Executing diagnosis workflow across failed cases...")
    execute_diagnosis(
        diag_id,
        Session,
        lambda: provider,
        max_cases=30,
        time_limit_seconds=120,
        max_retries=3,
    )

    with Session() as session:
        diag = session.scalar(select(Diagnosis).where(Diagnosis.id == diag_id))
        print(f"\n--- Diagnosis Completed (Status: {diag.status}) ---")
        print(f"Error: {diag.error}")
        print(f"Stats: {diag.stats}")

        hypotheses = list(
            session.scalars(
                select(DiagnosisHypothesis)
                .where(DiagnosisHypothesis.diagnosis_id == diag_id)
                .order_by(DiagnosisHypothesis.rank.asc())
            )
        )
        print(f"\nHypotheses Generated: {len(hypotheses)}")
        cat_counts = Counter(h.category for h in hypotheses)
        for cat, cnt in cat_counts.items():
            print(f"  {cat}: {cnt}")

        print("\nDetail of First 5 Hypotheses:")
        for h in hypotheses[:5]:
            print(f"  [Category: {h.category}] Conf: {h.confidence} Status: {h.evidence_status}")
            print(f"    Explanation: {h.explanation}")
            print(f"    Evidence Spans: {h.evidence_span_ids}")
            print(f"    Rec Experiment: {h.recommended_experiment}")

    return diag_id


if __name__ == "__main__":
    diagnose_latest_run()
