import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.path.abspath("backend"))

from app.core.config import get_settings
from app.db.session import make_engine, make_sessionmaker
from app.models import Dataset
from app.services import datasets as svc
from sqlalchemy import select

PROJECT_ID = uuid.UUID("9f7a4e00-c182-4eb6-8afb-49c80d1dbe3e")
USER_ID = uuid.UUID("803ed58e-90ca-4099-b2f0-10b168d9de06")
DATASET_NAME = "Customer Support QA Dataset"

def sync():
    scenarios_path = Path(__file__).parent / "scenarios" / "test_cases.json"
    with open(scenarios_path, "r", encoding="utf-8") as f:
        cases_content = f.read()

    settings = get_settings()
    engine = make_engine(settings.dev_database_url.get_secret_value())
    Session = make_sessionmaker(engine)

    with Session() as session:
        # Check if dataset already exists
        dataset = session.scalar(
            select(Dataset).where(Dataset.project_id == PROJECT_ID, Dataset.name == DATASET_NAME)
        )
        if not dataset:
            dataset = Dataset(
                project_id=PROJECT_ID,
                name=DATASET_NAME,
                description="Evaluation benchmark covering 12 customer support scenarios across all 8 diagnosis categories.",
                created_by=USER_ID,
            )
            session.add(dataset)
            session.commit()
            session.refresh(dataset)
            print(f"Created dataset: {dataset.id} ({dataset.name})")
        else:
            print(f"Existing dataset found: {dataset.id} ({dataset.name})")

        # Parse and import cases
        cases = svc.parse_import("json", cases_content)
        imported_count = svc.import_cases(session, dataset, cases, mode="replace")
        session.commit()
        print(f"Imported {imported_count} draft cases.")

        # Check if version exists or publish new version
        drafts = svc.draft_cases(session, dataset.id)
        latest_v = svc.latest_version(session, dataset.id)
        if not latest_v or svc.draft_hash(drafts) != latest_v.content_hash:
            version = svc.publish(session, dataset, notes="Version 3: 50-case generalization benchmark", user_id=USER_ID)
            session.commit()
            print(f"Published dataset version {version.version} with {version.case_count} cases.")
            v_num = version.version
        else:
            print(f"Latest version is v{latest_v.version} with {latest_v.case_count} cases.")
            v_num = latest_v.version

        return dataset.id, DATASET_NAME, v_num

if __name__ == "__main__":
    sync()
