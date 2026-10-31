"""Recovery of work interrupted by a crash: stale diagnoses, interrupted scoring and expired experiments.

A crash is simulated by leaving rows in the state a dead process would leave them in (committed ``pending``,
``running`` or ``scoring`` with no worker); the sweep is run with a clock moved past the relevant budget.
"""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.diagnosis.providers import MockProvider
from app.main import create_app
from app.models import Diagnosis, DiagnosisCase, EvaluationRun, Experiment
from app.services import evaluation as runs
from app.services.recovery import (
    EXPIRED_EXPERIMENT,
    INTERRUPTED_DIAGNOSIS,
    INTERRUPTED_SCORING,
    NEVER_STARTED_DIAGNOSIS,
    RecoveryWorker,
    recover_stale_work,
)
from tests.conftest import ApiClient
from tests.test_diagnosis_api import provider_slot, scored  # noqa: F401  (fixtures)
from tests.test_evaluation_runs import Env
from tests.test_experiments import create, env as experiment_env, headers  # noqa: F401  (fixture)

LATER = timedelta(hours=3)


def stuck_diagnosis(db: Session, env: Env, run: dict[str, Any], status: str, **fields: Any) -> uuid.UUID:
    """A diagnosis row exactly as a process that died before/while running it leaves it."""
    diagnosis = Diagnosis(project_id=uuid.UUID(env.project["id"]), run_id=uuid.UUID(run["id"]), provider="mock", **fields)
    db.add(diagnosis)
    db.commit()
    if status == "running":
        diagnosis.status, diagnosis.started_at = "running", utcnow()
        db.commit()
    return diagnosis.id


def test_stale_diagnoses_fail_with_a_clear_reason_and_can_be_retried(scored, db: Session, settings: Settings) -> None:
    env, run = scored
    running = stuck_diagnosis(db, env, run, "running")
    pending = stuck_diagnosis(db, env, run, "pending")
    assert recover_stale_work(db, settings).diagnoses == 0  # within budget: a live workflow could still own them

    report = recover_stale_work(db, settings, now=utcnow() + LATER)
    assert report.diagnoses == 2
    db.expire_all()
    for diagnosis_id, reason in ((running, INTERRUPTED_DIAGNOSIS), (pending, NEVER_STARTED_DIAGNOSIS)):
        body = env.alice.get(f"{env.base}/diagnoses/{diagnosis_id}").json()
        assert body["status"] == "failed" and body["error"] == reason and body["finished_at"] and body["cases"] == []

    retried = env.alice.post(f"{env.base}/diagnoses/{running}/retry")
    assert retried.status_code == 202
    assert env.alice.get(f"{env.base}/diagnoses/{retried.json()['id']}").json()["status"] == "completed"
    assert env.alice.get(f"{env.base}/diagnoses/{running}").json()["error"] == INTERRUPTED_DIAGNOSIS  # history kept


def test_recovery_never_touches_finished_diagnoses(scored, db: Session, settings: Settings) -> None:
    env, run = scored
    done = env.alice.post(f"{env.base}/eval-runs/{run['id']}/diagnoses").json()
    before = env.alice.get(f"{env.base}/diagnoses/{done['id']}").json()
    assert before["status"] == "completed"
    assert recover_stale_work(db, settings, now=utcnow() + LATER).diagnoses == 0
    assert env.alice.get(f"{env.base}/diagnoses/{done['id']}").json() == before


def test_a_workflow_that_loses_the_race_to_recovery_writes_nothing(scored, provider_slot, app: FastAPI, settings: Settings) -> None:  # noqa: F811
    """Recovery marks the diagnosis failed mid-flight; the still-live workflow must not complete or add rows."""
    env, run = scored

    class SweepingProvider(MockProvider):
        def propose(self, evidence: Any) -> Any:
            with app.state.sessionmaker() as other:
                recover_stale_work(other, settings, now=utcnow() + LATER)
            return super().propose(evidence)

    provider_slot["factory"] = lambda _settings: SweepingProvider()
    started = env.alice.post(f"{env.base}/eval-runs/{run['id']}/diagnoses")
    body = env.alice.get(f"{env.base}/diagnoses/{started.json()['id']}").json()
    assert body["status"] == "failed" and body["error"] == INTERRUPTED_DIAGNOSIS and body["cases"] == []
    with app.state.sessionmaker() as db:
        assert db.scalar(select(DiagnosisCase).where(DiagnosisCase.diagnosis_id == uuid.UUID(body["id"]))) is None


def interrupted_scoring(db: Session, env: Env) -> dict[str, Any]:
    """A run whose scorer died after ``begin_scoring`` committed (the scoring transaction rolled back)."""
    run = env.create_run()
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": ids["shipping"], "output": {"answer": "Your order is shipped."}}])
    row = runs.get_run(db, uuid.UUID(env.project["id"]), uuid.UUID(run["id"]), lock=True)
    runs.begin_scoring(db, row)
    db.commit()
    return run


def test_interrupted_scoring_fails_the_run_without_partial_scores(alice: ApiClient, client: ApiClient, db: Session, settings: Settings) -> None:
    env = Env(alice, client)
    run = interrupted_scoring(db, env)
    assert recover_stale_work(db, settings).scoring_runs == 0
    assert recover_stale_work(db, settings, now=utcnow() + LATER).scoring_runs == 1
    body = alice.get(f"{env.base}/eval-runs/{run['id']}").json()
    assert body["status"] == "failed" and body["failure_reason"] == INTERRUPTED_SCORING and body["pass_rate"] is None
    assert body["summary"] == {"received_cases": 1, "total_cases": 3}
    assert all(r["score"] is None for r in env.results(run))
    assert env.submit(run, []).status_code in (409, 422)
    assert alice.post(f"{env.base}/eval-runs/{run['id']}/rerun").status_code == 201


def test_live_scoring_holding_the_row_lock_is_skipped(alice: ApiClient, client: ApiClient, db: Session, engine: Engine, settings: Settings) -> None:
    env = Env(alice, client)
    run = interrupted_scoring(db, env)
    with Session(engine) as scorer:  # a live scorer holds the run row lock for its whole transaction
        scorer.scalar(select(EvaluationRun).where(EvaluationRun.id == uuid.UUID(run["id"])).with_for_update())
        with Session(engine) as sweeper:
            assert recover_stale_work(sweeper, settings, now=utcnow() + LATER).scoring_runs == 0
        scorer.rollback()
    assert alice.get(f"{env.base}/eval-runs/{run['id']}").json()["status"] == "scoring"


def test_expired_experiment_fails_and_its_arms_stop_accepting_results(experiment_env: Env, db: Session, settings: Settings) -> None:  # noqa: F811
    env = experiment_env
    exp = create(env).json()
    assert env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers=headers(env)).json()["status"] == "running"
    baseline = {"id": exp["baseline"]["run_id"]}
    ids = env.executor_cases(baseline)
    env.submit(baseline, [{"case_id": ids["shipping"], "output": {"answer": "x"}}])
    assert recover_stale_work(db, settings).experiments == 0

    assert recover_stale_work(db, settings, now=utcnow() + LATER).experiments == 1
    body = env.alice.get(f"{env.base}/experiments/{exp['id']}").json()
    assert body["status"] == "failed" and body["failure_reason"] == EXPIRED_EXPERIMENT and body["comparison"] == {}
    assert body["baseline"]["run_status"] == "failed" and body["candidate"]["run_status"] == "failed"
    assert env.submit(baseline, [{"case_id": ids["refunds"], "output": {"answer": "y"}}]).status_code == 409
    assert env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/complete", headers=headers(env)).status_code == 409
    decision = env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions", json={"decision": "approve", "rationale": "ship it"})
    assert decision.status_code == 409


def test_pending_experiments_and_runs_waiting_for_an_executor_are_left_alone(experiment_env: Env, db: Session, settings: Settings) -> None:  # noqa: F811
    env = experiment_env
    exp = create(env).json()
    waiting = env.create_run()
    assert recover_stale_work(db, settings, now=utcnow() + timedelta(days=30)).total == 0
    assert db.get(Experiment, uuid.UUID(exp["id"])).status == "pending"  # type: ignore[union-attr]
    assert env.alice.get(f"{env.base}/eval-runs/{waiting['id']}").json()["status"] == "pending"


def test_server_startup_recovers_work_left_by_a_crashed_process(scored, settings: Settings, db: Session) -> None:
    env, run = scored
    orphan = stuck_diagnosis(db, env, run, "pending", created_at=utcnow() - timedelta(hours=1))
    with ApiClient(create_app(settings), base_url="http://127.0.0.1:8000"):  # lifespan runs the startup sweep
        pass
    db.expire_all()
    diagnosis = db.get(Diagnosis, orphan)
    assert diagnosis is not None and diagnosis.status == "failed" and diagnosis.error == NEVER_STARTED_DIAGNOSIS


def test_a_failing_sweep_is_logged_and_does_not_raise(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    def broken() -> Session:
        raise RuntimeError("database unavailable")

    assert RecoveryWorker(broken, settings).sweep() is None  # type: ignore[arg-type]
    assert "recovery sweep failed" in caplog.text
