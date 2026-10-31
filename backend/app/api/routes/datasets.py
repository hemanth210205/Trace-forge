import json
import uuid
from typing import Literal

from fastapi import APIRouter, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CsrfAuth, CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.core.errors import AppError, NotFound
from app.db.errors import constraint_name
from app.models import Dataset, DatasetCase, DatasetVersion, DatasetVersionCase
from app.schemas.evaluation import (
    CaseCreate,
    CaseOut,
    CaseUpdate,
    DatasetCreate,
    DatasetOut,
    DatasetUpdate,
    ImportRequest,
    ImportResult,
    PublishRequest,
    VersionDetail,
    VersionSummary,
)
from app.services import datasets as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])
_NOT_FOUND = {404: {"description": "Project, dataset, case or version not found (or not owned)"}}


def _case_out(c: DatasetCase | DatasetVersionCase) -> CaseOut:
    return CaseOut(id=c.id, key=c.case_key, position=c.position, input=c.input, expected_output=c.expected_output,
                   expected_sources=c.expected_sources, metadata=c.metadata_)


def _version_summary(v: DatasetVersion) -> VersionSummary:
    return VersionSummary(id=v.id, version=v.version, case_count=v.case_count, content_hash=v.content_hash,
                          notes=v.notes, created_at=v.created_at)


def _dataset_out(db: DbSession, d: Dataset) -> DatasetOut:
    cases = svc.draft_cases(db, d.id)
    latest = svc.latest_version(db, d.id)
    return DatasetOut(
        id=d.id, project_id=d.project_id, name=d.name, description=d.description, metadata=d.metadata_,
        draft_case_count=len(cases), latest_version=_version_summary(latest) if latest else None,
        draft_changed_since_latest=bool(cases) and (latest is None or svc.draft_hash(cases) != latest.content_hash),
        created_at=d.created_at, updated_at=d.updated_at,
    )


def _flush_or_name_conflict(db: DbSession) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if constraint_name(exc) == "uq_datasets_project_id_name":
            raise AppError(409, "dataset_name_taken", "A dataset with this name already exists in the project.") from None
        raise


@router.get("", response_model=list[DatasetOut], responses=_NOT_FOUND)
def list_datasets(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> list[DatasetOut]:
    project = get_owned_project(db, ctx.user.id, project_id)
    datasets = db.scalars(select(Dataset).where(Dataset.project_id == project.id).order_by(Dataset.name))
    return [_dataset_out(db, d) for d in datasets]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DatasetOut, responses=_NOT_FOUND)
def create_dataset(project_id: uuid.UUID, body: DatasetCreate, ctx: CsrfAuth, db: DbSession) -> DatasetOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = Dataset(project_id=project.id, name=body.name, description=body.description, metadata_=body.metadata,
                      created_by=ctx.user.id)
    db.add(dataset)
    _flush_or_name_conflict(db)
    db.commit()
    return _dataset_out(db, dataset)


@router.get("/{dataset_id}", response_model=DatasetOut, responses=_NOT_FOUND)
def get_dataset(project_id: uuid.UUID, dataset_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> DatasetOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    return _dataset_out(db, svc.get_owned_dataset(db, project.id, dataset_id))


@router.patch("/{dataset_id}", response_model=DatasetOut, responses=_NOT_FOUND)
def update_dataset(project_id: uuid.UUID, dataset_id: uuid.UUID, body: DatasetUpdate, ctx: CsrfAuth, db: DbSession) -> DatasetOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        dataset.name = changes["name"].strip() or dataset.name
    if changes.get("description") is not None:
        dataset.description = changes["description"]
    if changes.get("metadata") is not None:
        dataset.metadata_ = changes["metadata"]
    _flush_or_name_conflict(db)
    db.commit()
    return _dataset_out(db, dataset)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def delete_dataset(project_id: uuid.UUID, dataset_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> Response:
    """Deletes the dataset with its versions and the evaluation runs that used them."""
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    versions = db.scalar(select(func.count()).select_from(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id))
    record_audit(db, action="dataset.deleted", target_type="dataset", target_id=dataset.id, actor_user_id=ctx.user.id,
                 project_id=project.id, details={"name": dataset.name, "versions": versions})
    db.delete(dataset)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- draft cases ----------------------------------------------------------------------------------------------


@router.get("/{dataset_id}/cases", response_model=list[CaseOut], responses=_NOT_FOUND)
def list_cases(project_id: uuid.UUID, dataset_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> list[CaseOut]:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id)
    return [_case_out(c) for c in svc.draft_cases(db, dataset.id)]


@router.post("/{dataset_id}/cases", status_code=status.HTTP_201_CREATED, response_model=CaseOut, responses=_NOT_FOUND)
def add_case(project_id: uuid.UUID, dataset_id: uuid.UUID, body: CaseCreate, ctx: CsrfAuth, db: DbSession) -> CaseOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    case = svc.add_case(db, dataset, body)
    db.commit()
    return _case_out(case)


def _owned_case(db: DbSession, dataset: Dataset, case_id: uuid.UUID) -> DatasetCase:
    case = db.scalar(select(DatasetCase).where(DatasetCase.id == case_id, DatasetCase.dataset_id == dataset.id))
    if case is None:
        raise NotFound("case")
    return case


@router.patch("/{dataset_id}/cases/{case_id}", response_model=CaseOut, responses=_NOT_FOUND)
def update_case(project_id: uuid.UUID, dataset_id: uuid.UUID, case_id: uuid.UUID, body: CaseUpdate, ctx: CsrfAuth,
                db: DbSession) -> CaseOut:
    """Edits the draft only; published versions are never affected (publish again for a new version)."""
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    case = _owned_case(db, dataset, case_id)
    changes = body.model_dump(exclude_unset=True)
    if "input" in changes and changes["input"] is None:
        raise AppError(422, "validation_error", "input cannot be null.")
    if changes.get("key") and changes["key"] != case.case_key:
        if db.scalar(select(DatasetCase.id).where(DatasetCase.dataset_id == dataset.id, DatasetCase.case_key == changes["key"])):
            raise AppError(409, "case_key_taken", f"A case with key '{changes['key']}' already exists in this dataset.")
        case.case_key = changes["key"]
    for field, attr in (("input", "input"), ("expected_output", "expected_output"), ("expected_sources", "expected_sources"),
                        ("metadata", "metadata_")):
        if field in changes and (changes[field] is not None or field == "expected_output"):
            setattr(case, attr, changes[field])
    db.commit()
    return _case_out(case)


@router.delete("/{dataset_id}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def delete_case(project_id: uuid.UUID, dataset_id: uuid.UUID, case_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> Response:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    db.delete(_owned_case(db, dataset, case_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{dataset_id}/import", response_model=ImportResult, responses=_NOT_FOUND)
def import_cases(project_id: uuid.UUID, dataset_id: uuid.UUID, body: ImportRequest, ctx: CsrfAuth, db: DbSession) -> ImportResult:
    """Validates the whole file first; any problem rejects it with every error and its line/item number."""
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    cases = svc.parse_import(body.format, body.content)
    imported = svc.import_cases(db, dataset, cases, body.mode)
    db.commit()
    return ImportResult(imported=imported, draft_case_count=len(svc.draft_cases(db, dataset.id)))


@router.get("/{dataset_id}/export", responses=_NOT_FOUND)
def export_cases(project_id: uuid.UUID, dataset_id: uuid.UUID, ctx: CurrentAuth, db: DbSession,
                 format: Literal["json", "jsonl"] = "jsonl", version: int | None = None) -> Response:
    """The draft, or a published version with ``version=N``, in a format the import accepts."""
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id)
    if version is None:
        lines, label = svc.export_lines(svc.draft_cases(db, dataset.id)), "draft"
    else:
        v = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id, DatasetVersion.version == version))
        if v is None:
            raise NotFound("version")
        lines = svc.export_lines(list(db.scalars(select(DatasetVersionCase).where(DatasetVersionCase.version_id == v.id)
                                                .order_by(DatasetVersionCase.position))))
        label = f"v{version}"
    if format == "jsonl":
        body, media = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), "application/x-ndjson"
    else:
        body, media = json.dumps({"cases": lines}, ensure_ascii=False, indent=2), "application/json"
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in dataset.name)[:60] or "dataset"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{safe}-{label}.{format}"'})


# --- versions ------------------------------------------------------------------------------------------------


@router.get("/{dataset_id}/versions", response_model=list[VersionSummary], responses=_NOT_FOUND)
def list_versions(project_id: uuid.UUID, dataset_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> list[VersionSummary]:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id)
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id).order_by(DatasetVersion.version.desc()))
    return [_version_summary(v) for v in versions]


@router.post("/{dataset_id}/versions", status_code=status.HTTP_201_CREATED, response_model=VersionSummary, responses=_NOT_FOUND)
def publish_version(project_id: uuid.UUID, dataset_id: uuid.UUID, body: PublishRequest, ctx: CsrfAuth, db: DbSession) -> VersionSummary:
    """Freezes the current draft as the next immutable version."""
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id, lock=True)
    version = svc.publish(db, dataset, body.notes, ctx.user.id)
    record_audit(db, action="dataset.version_published", target_type="dataset_version", target_id=version.id,
                 actor_user_id=ctx.user.id, project_id=project.id,
                 details={"dataset": dataset.name, "version": version.version, "cases": version.case_count})
    db.commit()
    return _version_summary(version)


@router.get("/{dataset_id}/versions/{version}", response_model=VersionDetail, responses=_NOT_FOUND)
def get_version(project_id: uuid.UUID, dataset_id: uuid.UUID, version: int, ctx: CurrentAuth, db: DbSession) -> VersionDetail:
    project = get_owned_project(db, ctx.user.id, project_id)
    dataset = svc.get_owned_dataset(db, project.id, dataset_id)
    v = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id, DatasetVersion.version == version))
    if v is None:
        raise NotFound("version")
    cases = db.scalars(select(DatasetVersionCase).where(DatasetVersionCase.version_id == v.id).order_by(DatasetVersionCase.position))
    return VersionDetail(**_version_summary(v).model_dump(), dataset_id=dataset.id, cases=[_case_out(c) for c in cases])
