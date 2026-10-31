"""Dataset drafts, JSON/JSONL import and export, and publishing immutable versions."""

import hashlib
import json
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.models import Dataset, DatasetCase, DatasetVersion, DatasetVersionCase
from app.schemas.evaluation import CaseCreate

MAX_IMPORT_CASES = 5000


def get_owned_dataset(db: Session, project_id: uuid.UUID, dataset_id: uuid.UUID, *, lock: bool = False) -> Dataset:
    stmt = select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id)
    dataset = db.scalar(stmt.with_for_update() if lock else stmt)
    if dataset is None:
        raise NotFound("dataset")
    return dataset


def draft_cases(db: Session, dataset_id: uuid.UUID) -> list[DatasetCase]:
    return list(db.scalars(select(DatasetCase).where(DatasetCase.dataset_id == dataset_id).order_by(DatasetCase.position)))


def _canonical(cases: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _case_dict(c: DatasetCase | DatasetVersionCase) -> dict[str, Any]:
    return {
        "key": c.case_key,
        "input": c.input,
        "expected_output": c.expected_output,
        "expected_sources": c.expected_sources,
        "metadata": c.metadata_,
    }


def draft_hash(cases: list[DatasetCase]) -> str:
    return _canonical([_case_dict(c) for c in cases])


def latest_version(db: Session, dataset_id: uuid.UUID) -> DatasetVersion | None:
    return db.scalar(
        select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version.desc()).limit(1)
    )


def _next_key(db: Session, dataset_id: uuid.UUID) -> str:
    count = db.scalar(select(func.count()).select_from(DatasetCase).where(DatasetCase.dataset_id == dataset_id)) or 0
    n = count + 1
    existing = set(db.scalars(select(DatasetCase.case_key).where(DatasetCase.dataset_id == dataset_id)))
    while f"case-{n}" in existing:
        n += 1
    return f"case-{n}"


def _next_position(db: Session, dataset_id: uuid.UUID) -> int:
    return (db.scalar(select(func.max(DatasetCase.position)).where(DatasetCase.dataset_id == dataset_id)) or 0) + 1


def add_case(db: Session, dataset: Dataset, data: CaseCreate) -> DatasetCase:
    key = data.key or _next_key(db, dataset.id)
    if db.scalar(select(DatasetCase.id).where(DatasetCase.dataset_id == dataset.id, DatasetCase.case_key == key)):
        raise AppError(409, "case_key_taken", f"A case with key '{key}' already exists in this dataset.")
    case = DatasetCase(
        dataset_id=dataset.id,
        case_key=key,
        position=_next_position(db, dataset.id),
        input=data.input,
        expected_output=data.expected_output,
        expected_sources=data.expected_sources,
        metadata_=data.metadata,
    )
    db.add(case)
    db.flush()
    return case


def parse_import(fmt: str, content: str) -> list[CaseCreate]:
    """All-or-nothing: returns every case or raises 422 listing each problem with its line/index."""
    errors: list[dict[str, Any]] = []
    raw: list[tuple[str, Any]] = []
    if fmt == "jsonl":
        for number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw.append((f"line {number}", json.loads(line)))
            except ValueError as exc:
                errors.append({"at": f"line {number}", "message": f"Invalid JSON: {exc.msg}."})
    else:
        try:
            document = json.loads(content)
        except ValueError as exc:
            raise AppError(422, "invalid_import", "The file is not valid JSON.", [{"at": f"line {exc.lineno}", "message": exc.msg}]) from None
        items = document.get("cases") if isinstance(document, dict) else document
        if not isinstance(items, list):
            raise AppError(422, "invalid_import", 'Expected a JSON array of cases or {"cases": [...]}.')
        raw = [(f"item {i}", item) for i, item in enumerate(items)]

    if len(raw) > MAX_IMPORT_CASES:
        raise AppError(422, "invalid_import", f"At most {MAX_IMPORT_CASES} cases can be imported at once.")
    cases: list[CaseCreate] = []
    seen: dict[str, str] = {}
    for where, item in raw:
        if not isinstance(item, dict):
            errors.append({"at": where, "message": "Each case must be a JSON object."})
            continue
        try:
            case = CaseCreate.model_validate(item)
        except ValidationError as exc:
            for err in exc.errors():
                field = ".".join(str(p) for p in err["loc"]) or "case"
                errors.append({"at": where, "message": f"{field}: {err['msg']}"})
            continue
        if case.key is not None:
            if case.key in seen:
                errors.append({"at": where, "message": f"Duplicate key '{case.key}' (also at {seen[case.key]})."})
                continue
            seen[case.key] = where
        cases.append(case)
    if errors:
        raise AppError(422, "invalid_import", f"{len(errors)} problem(s) found; nothing was imported.", errors[:100])
    if not cases:
        raise AppError(422, "invalid_import", "The file contains no cases.")
    return cases


def import_cases(db: Session, dataset: Dataset, cases: list[CaseCreate], mode: str) -> int:
    if mode == "replace":
        for existing in draft_cases(db, dataset.id):
            db.delete(existing)
        db.flush()
    else:
        taken = set(db.scalars(select(DatasetCase.case_key).where(DatasetCase.dataset_id == dataset.id)))
        clashes = [c.key for c in cases if c.key in taken]
        if clashes:
            raise AppError(422, "invalid_import", "Some keys already exist in the draft; nothing was imported.",
                           [{"at": f"key {k}", "message": "Key already exists (use mode=replace or change it)."} for k in clashes[:100]])
    for case in cases:
        add_case(db, dataset, case)
    return len(cases)


def export_lines(cases: list[DatasetCase] | list[DatasetVersionCase]) -> list[dict[str, Any]]:
    return [_case_dict(c) for c in cases]


def publish(db: Session, dataset: Dataset, notes: str, user_id: uuid.UUID) -> DatasetVersion:
    """Snapshots the draft into the next version. Caller holds the dataset row lock (serializes numbering)."""
    cases = draft_cases(db, dataset.id)
    if not cases:
        raise AppError(422, "empty_draft", "Add at least one case before publishing a version.")
    content_hash = draft_hash(cases)
    latest = latest_version(db, dataset.id)
    if latest is not None and latest.content_hash == content_hash:
        raise AppError(409, "no_changes", f"The draft is identical to version {latest.version}; nothing to publish.")
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=(latest.version if latest else 0) + 1,
        case_count=len(cases),
        content_hash=content_hash,
        notes=notes,
        created_by=user_id,
    )
    db.add(version)
    db.flush()
    db.add_all(
        DatasetVersionCase(
            version_id=version.id,
            case_key=c.case_key,
            position=i,
            input=c.input,
            expected_output=c.expected_output,
            expected_sources=c.expected_sources,
            metadata_=c.metadata_,
        )
        for i, c in enumerate(cases, start=1)
    )
    db.flush()
    return version
