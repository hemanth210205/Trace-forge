"""Failure diagnosis of evaluation runs (console session; owner only)."""

import uuid
from collections import Counter
from collections.abc import Callable

from fastapi import APIRouter, BackgroundTasks, Request, status
from sqlalchemy import select

from app.api.deps import AppSettings, CsrfAuth, CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.core.config import Settings
from app.core.errors import AppError, NotFound
from app.diagnosis.providers import DiagnosisProvider, ProviderConfigurationError, build_provider
from app.diagnosis.workflow import execute_diagnosis
from app.models import Diagnosis, DiagnosisCase, DiagnosisHypothesis, EvaluationRun
from app.schemas.diagnosis import DiagnosisCaseOut, DiagnosisDetail, DiagnosisSummary, HypothesisDetail, HypothesisOut
from app.services.evaluation import get_run

router = APIRouter(prefix="/projects/{project_id}", tags=["diagnosis"])
_NOT_FOUND = {404: {"description": "Project, run, diagnosis or hypothesis not found (or not owned)"}}


def _provider_factory(request: Request) -> Callable[[Settings], DiagnosisProvider]:
    # Tests may install a different factory on app.state; production uses the configured provider.
    return getattr(request.app.state, "diagnosis_provider_factory", build_provider)


def _hypothesis_out(h: DiagnosisHypothesis) -> HypothesisOut:
    return HypothesisOut(
        id=h.id, case_id=h.case_id, rank=h.rank, category=h.category, explanation=h.explanation,
        supporting_facts=h.supporting_facts, contradicting_facts=h.contradicting_facts,
        evidence_trace_ids=h.evidence_trace_ids, evidence_span_ids=h.evidence_span_ids, evidence_status=h.evidence_status,
        confidence=h.confidence, uncertainty=h.uncertainty, missing_evidence=h.missing_evidence,
        recommended_experiment=h.recommended_experiment, validation_status=h.validation_status, validated_by=h.validated_by,
    )


def _summary(db: DbSession, d: Diagnosis) -> dict:
    run_name = db.scalar(select(EvaluationRun.name).where(EvaluationRun.id == d.run_id)) or ""
    categories = Counter(db.scalars(
        select(DiagnosisHypothesis.category).where(DiagnosisHypothesis.diagnosis_id == d.id, DiagnosisHypothesis.evidence_status == "supported")
    ))
    return {
        "id": d.id, "project_id": d.project_id, "run_id": d.run_id, "run_name": run_name, "retry_of": d.retry_of,
        "status": d.status, "provider": d.provider, "model": d.model, "created_at": d.created_at, "started_at": d.started_at,
        "finished_at": d.finished_at, "error": d.error, "summary": d.summary, "stats": d.stats, "category_counts": dict(categories),
    }


def _owned_diagnosis(db: DbSession, project_id: uuid.UUID, diagnosis_id: uuid.UUID) -> Diagnosis:
    diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.id == diagnosis_id, Diagnosis.project_id == project_id))
    if diagnosis is None:
        raise NotFound("diagnosis")
    return diagnosis


def _start(request: Request, db: DbSession, settings: Settings, background: BackgroundTasks, project_id: uuid.UUID,
           run: EvaluationRun, user_id: uuid.UUID, retry_of: uuid.UUID | None) -> Diagnosis:
    if run.status not in ("completed", "incomplete"):
        raise AppError(409, "run_not_scored", f"Only scored runs can be diagnosed; this run is {run.status}.")
    if not (run.summary or {}).get("failed"):
        raise AppError(409, "nothing_to_diagnose", "This run has no failed cases.")
    try:
        provider = _provider_factory(request)(settings)
    except ProviderConfigurationError as exc:
        raise AppError(503, "diagnosis_provider_unavailable", str(exc)) from None
    diagnosis = Diagnosis(project_id=project_id, run_id=run.id, retry_of=retry_of, provider=provider.name,
                          model=provider.model, created_by=user_id)
    db.add(diagnosis)
    db.commit()
    background.add_task(
        execute_diagnosis, diagnosis.id, request.app.state.sessionmaker, lambda: provider,
        max_cases=settings.diagnosis_max_cases, time_limit_seconds=settings.diagnosis_time_limit_seconds,
        max_retries=settings.diagnosis_provider_max_retries,
    )
    return diagnosis


@router.post("/eval-runs/{run_id}/diagnoses", status_code=status.HTTP_202_ACCEPTED, response_model=DiagnosisSummary, responses=_NOT_FOUND)
def start_diagnosis(project_id: uuid.UUID, run_id: uuid.UUID, request: Request, background: BackgroundTasks,
                    ctx: CsrfAuth, db: DbSession, settings: AppSettings) -> DiagnosisSummary:
    """Starts a diagnosis of the run's failed cases. It runs in the background; poll the returned diagnosis."""
    project = get_owned_project(db, ctx.user.id, project_id)
    run = get_run(db, project.id, run_id)
    diagnosis = _start(request, db, settings, background, project.id, run, ctx.user.id, None)
    return DiagnosisSummary(**_summary(db, diagnosis))


@router.post("/diagnoses/{diagnosis_id}/retry", status_code=status.HTTP_202_ACCEPTED, response_model=DiagnosisSummary, responses=_NOT_FOUND)
def retry_diagnosis(project_id: uuid.UUID, diagnosis_id: uuid.UUID, request: Request, background: BackgroundTasks,
                    ctx: CsrfAuth, db: DbSession, settings: AppSettings) -> DiagnosisSummary:
    """A new diagnosis of the same run; the earlier one is kept unchanged."""
    project = get_owned_project(db, ctx.user.id, project_id)
    previous = _owned_diagnosis(db, project.id, diagnosis_id)
    if previous.status in ("pending", "running"):
        raise AppError(409, "diagnosis_in_progress", "This diagnosis is still running.")
    run = get_run(db, project.id, previous.run_id)
    diagnosis = _start(request, db, settings, background, project.id, run, ctx.user.id, previous.id)
    return DiagnosisSummary(**_summary(db, diagnosis))


@router.get("/diagnoses", response_model=list[DiagnosisSummary], responses=_NOT_FOUND)
def list_diagnoses(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession, run_id: uuid.UUID | None = None) -> list[DiagnosisSummary]:
    project = get_owned_project(db, ctx.user.id, project_id)
    stmt = select(Diagnosis).where(Diagnosis.project_id == project.id)
    if run_id:
        stmt = stmt.where(Diagnosis.run_id == run_id)
    return [DiagnosisSummary(**_summary(db, d)) for d in db.scalars(stmt.order_by(Diagnosis.created_at.desc()).limit(200))]


@router.get("/diagnoses/{diagnosis_id}", response_model=DiagnosisDetail, responses=_NOT_FOUND)
def get_diagnosis(project_id: uuid.UUID, diagnosis_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> DiagnosisDetail:
    project = get_owned_project(db, ctx.user.id, project_id)
    diagnosis = _owned_diagnosis(db, project.id, diagnosis_id)
    cases = db.scalars(select(DiagnosisCase).where(DiagnosisCase.diagnosis_id == diagnosis.id).order_by(DiagnosisCase.position))
    hypotheses: dict[uuid.UUID, list[HypothesisOut]] = {}
    for h in db.scalars(select(DiagnosisHypothesis).where(DiagnosisHypothesis.diagnosis_id == diagnosis.id).order_by(DiagnosisHypothesis.rank)):
        hypotheses.setdefault(h.case_id, []).append(_hypothesis_out(h))
    return DiagnosisDetail(
        **_summary(db, diagnosis),
        cases=[DiagnosisCaseOut(id=c.id, result_id=c.result_id, position=c.position, case_key=c.case_key, trace_id=c.trace_id,
                                summary=c.summary, facts=c.facts, missing_evidence=c.missing_evidence,
                                hypotheses=hypotheses.get(c.id, [])) for c in cases],
    )


@router.get("/diagnoses/{diagnosis_id}/hypotheses/{hypothesis_id}", response_model=HypothesisDetail, responses=_NOT_FOUND)
def get_hypothesis(project_id: uuid.UUID, diagnosis_id: uuid.UUID, hypothesis_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> HypothesisDetail:
    project = get_owned_project(db, ctx.user.id, project_id)
    diagnosis = _owned_diagnosis(db, project.id, diagnosis_id)
    row = db.execute(
        select(DiagnosisHypothesis, DiagnosisCase).join(DiagnosisCase, DiagnosisCase.id == DiagnosisHypothesis.case_id)
        .where(DiagnosisHypothesis.id == hypothesis_id, DiagnosisHypothesis.diagnosis_id == diagnosis.id)
    ).first()
    if row is None:
        raise NotFound("hypothesis")
    h, case = row
    cited = set(h.supporting_facts) | set(h.contradicting_facts)
    return HypothesisDetail(**_hypothesis_out(h).model_dump(), diagnosis_id=diagnosis.id, case_key=case.case_key,
                            trace_id=case.trace_id, facts=[f for f in case.facts if f.get("id") in cited])
