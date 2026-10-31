"""Evaluation runs: console routes (session) and executor routes for the SDK (project API key)."""

import uuid
from typing import Literal

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import CsrfAuth, CurrentApiKey, CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.core.errors import AppError, NotFound
from app.models import Dataset, DatasetVersion, DatasetVersionCase, EvaluationResult, EvaluationRun, Trace
from app.schemas.evaluation import (
    CaseResultOut,
    ExecutorCase,
    ExecutorRun,
    ResultsSubmit,
    RunCreate,
    RunOut,
    SdkRunCreate,
    SubmitAck,
)
from app.services import evaluation as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/projects/{project_id}/eval-runs", tags=["evaluations"])
sdk_router = APIRouter(prefix="/sdk/eval-runs", tags=["evaluations (SDK)"])
_NOT_FOUND = {404: {"description": "Project, run or dataset version not found (or not owned)"}}


def _owned_version(db: DbSession, project_id: uuid.UUID, version_id: uuid.UUID) -> DatasetVersion:
    version = db.scalar(
        select(DatasetVersion).join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.id == version_id, Dataset.project_id == project_id)
    )
    if version is None:
        raise NotFound("dataset_version")
    return version


# --- console ---------------------------------------------------------------------------------------------------


@router.get("", response_model=list[RunOut], responses=_NOT_FOUND)
def list_runs(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession, dataset_id: uuid.UUID | None = None) -> list[RunOut]:
    project = get_owned_project(db, ctx.user.id, project_id)
    stmt = select(EvaluationRun).where(EvaluationRun.project_id == project.id)
    if dataset_id:
        stmt = stmt.join(DatasetVersion, DatasetVersion.id == EvaluationRun.dataset_version_id).where(
            DatasetVersion.dataset_id == dataset_id
        )
    runs = db.scalars(stmt.order_by(EvaluationRun.created_at.desc()).limit(200))
    return [RunOut(**svc.run_out(db, r)) for r in runs]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RunOut, responses=_NOT_FOUND)
def create_run(project_id: uuid.UUID, body: RunCreate, ctx: CsrfAuth, db: DbSession) -> RunOut:
    """A pending run over an exact dataset version. Execute it with ``traceforge.evaluate(run_id, task)``."""
    project = get_owned_project(db, ctx.user.id, project_id)
    version = _owned_version(db, project.id, body.dataset_version_id)
    run = svc.create_run(db, project.id, version, body.name, body.evaluators, ctx.user.id)
    db.commit()
    return RunOut(**svc.run_out(db, run))


@router.get("/{run_id}", response_model=RunOut, responses=_NOT_FOUND)
def get_run(project_id: uuid.UUID, run_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> RunOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    return RunOut(**svc.run_out(db, svc.get_run(db, project.id, run_id)))


@router.get("/{run_id}/results", response_model=list[CaseResultOut], responses=_NOT_FOUND)
def list_results(
    project_id: uuid.UUID, run_id: uuid.UUID, ctx: CurrentAuth, db: DbSession,
    state: Literal["passed", "failed", "not_evaluated", "missing", "pending"] | None = None,
) -> list[CaseResultOut]:
    """Every case of the run's dataset version with its result; cases without a result are ``missing``
    (or ``pending`` while the run still accepts results)."""
    project = get_owned_project(db, ctx.user.id, project_id)
    run = svc.get_run(db, project.id, run_id)
    rows = db.execute(
        select(DatasetVersionCase, EvaluationResult)
        .outerjoin(EvaluationResult, (EvaluationResult.case_id == DatasetVersionCase.id) & (EvaluationResult.run_id == run.id))
        .where(DatasetVersionCase.version_id == run.dataset_version_id)
        .order_by(DatasetVersionCase.position)
    ).all()
    trace_ids = {r.trace_id for _, r in rows if r is not None and r.trace_id}
    existing = set(db.scalars(select(Trace.id).where(Trace.project_id == project.id, Trace.id.in_(trace_ids)))) if trace_ids else set()
    out: list[CaseResultOut] = []
    for case, result in rows:
        if result is None:
            case_state = "pending" if run.status in svc.ACCEPTING else "missing"
        elif result.scored_at is None:
            case_state = "pending"
        else:
            case_state = "not_evaluated" if result.passed is None else "passed" if result.passed else "failed"
        if state and case_state != state:
            continue
        out.append(CaseResultOut(
            case_id=case.id, key=case.case_key, position=case.position, input=case.input,
            expected_output=case.expected_output, expected_sources=case.expected_sources, state=case_state,
            output=result.output if result else None, execution_error=result.execution_error if result else None,
            trace_id=result.trace_id if result and result.trace_id in existing else None,
            latency_ms=result.latency_ms if result else None, score=result.score if result else None,
            evaluations=result.evaluations if result else [], submitted_at=result.submitted_at if result else None,
        ))
    return out


@router.post("/{run_id}/finish", response_model=RunOut, responses=_NOT_FOUND)
def finish_run(project_id: uuid.UUID, run_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> RunOut:
    """Stops accepting results and scores. Cases without a result make the run ``incomplete``."""
    project = get_owned_project(db, ctx.user.id, project_id)
    return RunOut(**svc.run_out(db, svc.finish_run(db, project.id, run_id)))


@router.post("/{run_id}/abort", response_model=RunOut, responses=_NOT_FOUND)
def abort_run(project_id: uuid.UUID, run_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> RunOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    run = svc.abort_run(db, project.id, run_id, f"Aborted by {ctx.user.email}.")
    record_audit(db, action="evaluation_run.aborted", target_type="evaluation_run", target_id=run.id,
                 actor_user_id=ctx.user.id, project_id=project.id, details={"name": run.name})
    db.commit()
    return RunOut(**svc.run_out(db, run))


@router.post("/{run_id}/rerun", status_code=status.HTTP_201_CREATED, response_model=RunOut, responses=_NOT_FOUND)
def rerun(project_id: uuid.UUID, run_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> RunOut:
    """A new pending run with the same dataset version and evaluators; the original stays unchanged."""
    project = get_owned_project(db, ctx.user.id, project_id)
    source = svc.get_run(db, project.id, run_id)
    version = _owned_version(db, project.id, source.dataset_version_id)
    run = svc.create_run(db, project.id, version, f"{source.name} (re-run)"[:200], source.evaluators, ctx.user.id)
    db.commit()
    return RunOut(**svc.run_out(db, run))


# --- executor (SDK, project API key) ----------------------------------------------------------------------------


@sdk_router.post("", status_code=status.HTTP_201_CREATED, response_model=RunOut,
                 responses={404: {"description": "Dataset or version not found in the key's project"}})
def sdk_create_run(body: SdkRunCreate, ctx: CurrentApiKey, db: DbSession) -> RunOut:
    dataset = db.scalar(select(Dataset).where(Dataset.project_id == ctx.project.id, Dataset.name == body.dataset))
    if dataset is None:
        raise NotFound("dataset")
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
    stmt = stmt.where(DatasetVersion.version == body.version) if body.version else stmt.order_by(DatasetVersion.version.desc())
    version = db.scalar(stmt.limit(1))
    if version is None:
        raise AppError(404, "version_not_found", "The dataset has no such published version.")
    run = svc.create_run(db, ctx.project.id, version, body.name, body.evaluators, None)
    db.commit()
    return RunOut(**svc.run_out(db, run))


@sdk_router.get("/{run_id}", response_model=ExecutorRun, responses=_NOT_FOUND)
def sdk_get_run(run_id: uuid.UUID, ctx: CurrentApiKey, db: DbSession) -> ExecutorRun:
    """The cases to execute. Expected outputs and sources are deliberately not included."""
    run = svc.get_run(db, ctx.project.id, run_id)
    info = svc.run_out(db, run)
    cases = db.scalars(select(DatasetVersionCase).where(DatasetVersionCase.version_id == run.dataset_version_id)
                       .order_by(DatasetVersionCase.position))
    return ExecutorRun(
        id=run.id, name=run.name, status=run.status, dataset_name=info["dataset_name"], dataset_version=info["dataset_version"],
        cases=[ExecutorCase(case_id=c.id, key=c.case_key, input=c.input, metadata=c.metadata_) for c in cases],
    )


@sdk_router.post("/{run_id}/results", response_model=SubmitAck, responses=_NOT_FOUND)
def sdk_submit(run_id: uuid.UUID, body: ResultsSubmit, ctx: CurrentApiKey, db: DbSession) -> SubmitAck:
    run = svc.get_run(db, ctx.project.id, run_id, lock=True)
    accepted = svc.submit_results(db, run, body.results)
    db.commit()
    return SubmitAck(accepted=accepted, status=run.status, received_cases=svc.received_count(db, run.id), total_cases=run.total_cases)


@sdk_router.post("/{run_id}/finish", response_model=RunOut, responses=_NOT_FOUND)
def sdk_finish(run_id: uuid.UUID, ctx: CurrentApiKey, db: DbSession) -> RunOut:
    return RunOut(**svc.run_out(db, svc.finish_run(db, ctx.project.id, run_id)))
