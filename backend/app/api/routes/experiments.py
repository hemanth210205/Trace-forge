"""Controlled experiments: console routes (session, owner only) and executor routes for the SDK (API key)."""

import uuid
from typing import Any

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.deps import AppSettings, CsrfAuth, CurrentApiKey, CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.models import Dataset, DatasetVersion, EvaluationRun, Experiment, ExperimentDecision, User
from app.schemas.experiment import (
    ArmCaseOut,
    ArmOut,
    DecisionCreate,
    DecisionOut,
    ExecutorArm,
    ExecutorExperiment,
    ExperimentCaseOut,
    ExperimentCreate,
    ExperimentOut,
    LinkedHypothesis,
)
from app.services import evaluation as runs
from app.services import experiments as svc

router = APIRouter(prefix="/projects/{project_id}/experiments", tags=["experiments"])
sdk_router = APIRouter(prefix="/sdk/experiments", tags=["experiments (SDK)"])
_NOT_FOUND = {404: {"description": "Project, run, hypothesis or experiment not found (or not owned)"}}


def _arm(db: DbSession, run_id: uuid.UUID, config: dict[str, Any]) -> ArmOut:
    run = db.get(EvaluationRun, run_id)
    assert run is not None
    return ArmOut(run_id=run.id, config=config, run_status=run.status, received_cases=runs.received_count(db, run.id), total_cases=run.total_cases)


def _out(db: DbSession, e: Experiment) -> ExperimentOut:
    version, dataset = db.execute(
        select(DatasetVersion, Dataset).join(Dataset, Dataset.id == DatasetVersion.dataset_id).where(DatasetVersion.id == e.dataset_version_id)
    ).one()
    source = db.get(EvaluationRun, e.source_run_id)
    hypothesis, validation = None, None
    if e.hypothesis_id is not None:
        found = svc.hypothesis_case_key(db, e.hypothesis_id)
        if found is not None:
            h, case_key = found
            hypothesis = LinkedHypothesis(
                id=h.id, diagnosis_id=h.diagnosis_id, case_key=case_key, category=h.category, explanation=h.explanation,
                evidence_status=h.evidence_status, validation_status=h.validation_status, validated_by=h.validated_by,
                recommended_experiment=h.recommended_experiment,
            )
            eligible, reason = svc.validation_eligibility(e, case_key)
            validation = {"eligible": eligible and h.validation_status != "validated", "reason": reason}
    decisions = db.execute(
        select(ExperimentDecision, User.display_name).outerjoin(User, User.id == ExperimentDecision.decided_by)
        .where(ExperimentDecision.experiment_id == e.id).order_by(ExperimentDecision.decided_at, ExperimentDecision.id)
    ).all()
    return ExperimentOut(
        id=e.id, project_id=e.project_id, name=e.name, description=e.description, status=e.status,
        source_run_id=e.source_run_id, source_run_name=source.name if source else "", dataset_id=dataset.id, dataset_name=dataset.name,
        dataset_version_id=version.id, dataset_version=version.version,
        baseline=_arm(db, e.baseline_run_id, e.baseline_config), candidate=_arm(db, e.candidate_run_id, e.candidate_config),
        config_diff=e.config_diff, hypothesis=hypothesis, hypothesis_validation=validation, created_at=e.created_at,
        started_at=e.started_at, deadline_at=e.deadline_at, finished_at=e.finished_at, failure_reason=e.failure_reason,
        comparison=e.comparison,
        decisions=[DecisionOut(id=d.id, decision=d.decision, rationale=d.rationale, decided_by=name, decided_at=d.decided_at) for d, name in decisions],
    )


# --- console ----------------------------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ExperimentOut, responses=_NOT_FOUND)
def create_experiment(project_id: uuid.UUID, body: ExperimentCreate, ctx: CsrfAuth, db: DbSession, settings: AppSettings) -> ExperimentOut:
    """Creates the experiment and its two pending arm runs on the source run's exact dataset version."""
    project = get_owned_project(db, ctx.user.id, project_id)
    experiment = svc.create_experiment(
        db, project_id=project.id, source_run_id=body.source_run_id, name=body.name.strip(), description=body.description,
        baseline_config=body.baseline_config, candidate_config=body.candidate_config, hypothesis_id=body.hypothesis_id,
        user_id=ctx.user.id, max_cases=settings.experiment_max_cases,
    )
    db.commit()
    return _out(db, experiment)


@router.get("", response_model=list[ExperimentOut], responses=_NOT_FOUND)
def list_experiments(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession, source_run_id: uuid.UUID | None = None) -> list[ExperimentOut]:
    project = get_owned_project(db, ctx.user.id, project_id)
    stmt = select(Experiment).where(Experiment.project_id == project.id)
    if source_run_id:
        stmt = stmt.where(Experiment.source_run_id == source_run_id)
    return [_out(db, e) for e in db.scalars(stmt.order_by(Experiment.created_at.desc()).limit(100))]


@router.get("/{experiment_id}", response_model=ExperimentOut, responses=_NOT_FOUND)
def get_experiment(project_id: uuid.UUID, experiment_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> ExperimentOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    return _out(db, svc.get_experiment(db, project.id, experiment_id))


@router.get("/{experiment_id}/cases", response_model=list[ExperimentCaseOut], responses=_NOT_FOUND)
def experiment_cases(project_id: uuid.UUID, experiment_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> list[ExperimentCaseOut]:
    """Baseline versus candidate for every case of the dataset version (computed from the immutable arm results)."""
    project = get_owned_project(db, ctx.user.id, project_id)
    e = svc.get_experiment(db, project.id, experiment_id)
    base = svc.arm_case_rows(db, project.id, db.get(EvaluationRun, e.baseline_run_id))  # type: ignore[arg-type]
    cand = svc.arm_case_rows(db, project.id, db.get(EvaluationRun, e.candidate_run_id))  # type: ignore[arg-type]
    arm_fields = ("state", "score", "output", "execution_error", "evaluations", "trace_id", "latency_ms", "tokens", "cost_usd")
    return [
        ExperimentCaseOut(
            case_key=key, position=b["position"], input=b["input"], expected_output=b["expected_output"],
            change=svc.case_change(b["state"], cand[key]["state"]), evaluator_changes=svc.evaluator_changes(b, cand[key]),
            baseline=ArmCaseOut(**{f: b[f] for f in arm_fields}), candidate=ArmCaseOut(**{f: cand[key][f] for f in arm_fields}),
        )
        for key, b in base.items()
    ]


@router.post("/{experiment_id}/complete", response_model=ExperimentOut, responses=_NOT_FOUND)
def complete_experiment(project_id: uuid.UUID, experiment_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> ExperimentOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    experiment = svc.get_experiment(db, project.id, experiment_id, lock=True)
    svc.complete(db, experiment)
    db.commit()
    return _out(db, experiment)


@router.post("/{experiment_id}/abort", response_model=ExperimentOut, responses=_NOT_FOUND)
def abort_experiment(project_id: uuid.UUID, experiment_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> ExperimentOut:
    project = get_owned_project(db, ctx.user.id, project_id)
    experiment = svc.get_experiment(db, project.id, experiment_id, lock=True)
    svc.abort(db, experiment, f"Aborted by {ctx.user.email}.")
    db.commit()
    return _out(db, experiment)


@router.post("/{experiment_id}/decisions", status_code=status.HTTP_201_CREATED, response_model=ExperimentOut, responses=_NOT_FOUND)
def decide(project_id: uuid.UUID, experiment_id: uuid.UUID, body: DecisionCreate, ctx: CsrfAuth, db: DbSession) -> ExperimentOut:
    """Records a human promotion decision. It changes no agent, configuration or deployment."""
    project = get_owned_project(db, ctx.user.id, project_id)
    experiment = svc.get_experiment(db, project.id, experiment_id, lock=True)
    svc.record_decision(db, experiment, body.decision, body.rationale, ctx.user.id)
    db.commit()
    return _out(db, experiment)


@router.post("/{experiment_id}/validate-hypothesis", response_model=ExperimentOut, responses=_NOT_FOUND)
def validate_hypothesis(project_id: uuid.UUID, experiment_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> ExperimentOut:
    """Marks the tested hypothesis validated, only if the baseline reproduced its failure and the candidate fixed it."""
    project = get_owned_project(db, ctx.user.id, project_id)
    experiment = svc.get_experiment(db, project.id, experiment_id, lock=True)
    svc.validate_hypothesis(db, experiment, ctx.user.id)
    db.commit()
    return _out(db, experiment)


# --- executor (SDK) -----------------------------------------------------------------------------------------------


def _executor(db: DbSession, e: Experiment) -> ExecutorExperiment:
    def arm(run_id: uuid.UUID, config: dict[str, Any]) -> ExecutorArm:
        run = db.get(EvaluationRun, run_id)
        return ExecutorArm(run_id=run_id, run_status=run.status if run else "missing", config=config)

    return ExecutorExperiment(id=e.id, name=e.name, status=e.status, baseline=arm(e.baseline_run_id, e.baseline_config),
                              candidate=arm(e.candidate_run_id, e.candidate_config))


@sdk_router.get("/{experiment_id}", response_model=ExecutorExperiment, responses=_NOT_FOUND)
def sdk_get(experiment_id: uuid.UUID, ctx: CurrentApiKey, db: DbSession) -> ExecutorExperiment:
    return _executor(db, svc.get_experiment(db, ctx.project.id, experiment_id))


@sdk_router.post("/{experiment_id}/start", response_model=ExecutorExperiment, responses=_NOT_FOUND)
def sdk_start(experiment_id: uuid.UUID, ctx: CurrentApiKey, db: DbSession, settings: AppSettings) -> ExecutorExperiment:
    experiment = svc.get_experiment(db, ctx.project.id, experiment_id, lock=True)
    svc.start(db, experiment, settings.experiment_time_limit_seconds)
    db.commit()
    return _executor(db, experiment)


@sdk_router.post("/{experiment_id}/complete", response_model=ExperimentOut, responses=_NOT_FOUND)
def sdk_complete(experiment_id: uuid.UUID, ctx: CurrentApiKey, db: DbSession) -> ExperimentOut:
    experiment = svc.get_experiment(db, ctx.project.id, experiment_id, lock=True)
    svc.complete(db, experiment)
    db.commit()
    return _out(db, experiment)

