"""Recovery of work interrupted by a crash or restart (local-first: one in-process sweeper, no job queue).

What can be left behind, and how each case is detected without guessing:

- **Diagnosis ``pending``** — the background task never started (the process died after the request). Failed
  once it is older than the grace period; a live task picks a pending diagnosis up within milliseconds.
- **Diagnosis ``running``** — the workflow died mid-way. A live workflow stops itself at its time limit (plus at
  most one provider call), so a diagnosis running longer than ``time limit + provider timeout + grace`` is dead.
- **Evaluation run ``scoring``** — scoring is synchronous and holds the run's row lock for its whole transaction.
  A ``scoring`` row that is *not* locked and is older than the grace period has no scorer. Locked rows are
  skipped (``SKIP LOCKED``), so a slow but live scoring is never touched.
- **Experiment ``running`` past ``deadline_at``** — failed, and its unfinished arm runs are aborted so they stop
  accepting results.

Every transition goes through the same state machines (and database triggers) as normal requests, rows are
locked with ``SKIP LOCKED`` so a sweep never blocks or races a live transition, and terminal records are never
touched. Pending experiments and pending/running evaluation runs wait for an external executor (the SDK) and are
left alone: there is no way to tell an idle executor from a dead one.
"""

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import utcnow
from app.models import Diagnosis, EvaluationRun, Experiment
from app.services import evaluation as runs

logger = logging.getLogger("traceforge.recovery")

INTERRUPTED_DIAGNOSIS = "Interrupted: the diagnosis stopped without finishing (server restart or crash). Retry to run it again."
NEVER_STARTED_DIAGNOSIS = "Interrupted: the diagnosis never started (server restart or crash). Retry to run it again."
INTERRUPTED_SCORING = "Interrupted: scoring stopped without finishing (server restart or crash). Re-run the evaluation."
EXPIRED_EXPERIMENT = "The experiment exceeded its time limit before both arms were scored."
EXPIRED_ARM = "Experiment time limit exceeded."


@dataclass
class RecoveryReport:
    diagnoses: int = 0
    scoring_runs: int = 0
    experiments: int = 0

    @property
    def total(self) -> int:
        return self.diagnoses + self.scoring_runs + self.experiments


def recover_stale_work(db: Session, settings: Settings, now: datetime | None = None) -> RecoveryReport:
    """One sweep: one transaction per kind of record, holding the row locks of everything it changes."""
    now = now or utcnow()
    grace = timedelta(seconds=settings.stale_job_grace_seconds)
    report = RecoveryReport()

    diagnosis_budget = timedelta(seconds=settings.diagnosis_time_limit_seconds + settings.diagnosis_provider_timeout_seconds) + grace
    stale_diagnoses = (
        select(Diagnosis)
        .where(((Diagnosis.status == "pending") & (Diagnosis.created_at < now - grace))
               | ((Diagnosis.status == "running") & (Diagnosis.started_at < now - diagnosis_budget)))
        .with_for_update(skip_locked=True).limit(100)
    )
    for diagnosis in db.scalars(stale_diagnoses).all():
        diagnosis.error = NEVER_STARTED_DIAGNOSIS if diagnosis.status == "pending" else INTERRUPTED_DIAGNOSIS
        diagnosis.finished_at = utcnow()
        diagnosis.status = "failed"
        report.diagnoses += 1
    db.commit()

    stale_scoring = (
        select(EvaluationRun)
        .where(EvaluationRun.status == "scoring", EvaluationRun.scoring_started_at < now - grace)
        .with_for_update(skip_locked=True).limit(100)
    )
    for run in db.scalars(stale_scoring).all():
        # Results of an interrupted scoring were rolled back with its transaction; nothing partial is kept.
        runs._mark_failed(db, run, INTERRUPTED_SCORING)
        db.flush()
        report.scoring_runs += 1
    db.commit()

    expired = (
        select(Experiment).where(Experiment.status == "running", Experiment.deadline_at < now)
        .with_for_update(skip_locked=True).limit(100)
    )
    for experiment in db.scalars(expired).all():
        for run_id in (experiment.baseline_run_id, experiment.candidate_run_id):
            run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
            if run is not None and run.status not in runs.TERMINAL:
                runs._mark_failed(db, run, EXPIRED_ARM)
                db.flush()
        experiment.failure_reason = EXPIRED_EXPERIMENT
        experiment.finished_at = utcnow()
        experiment.status = "failed"
        report.experiments += 1
    db.commit()

    if report.total:
        logger.warning("recovered interrupted work: %s", asdict(report))
    return report


class RecoveryWorker:
    """Runs a sweep at startup and then every ``interval`` seconds on a daemon thread (0 = startup only)."""

    def __init__(self, factory: sessionmaker[Session], settings: Settings) -> None:
        self.factory = factory
        self.settings = settings
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sweep(self) -> RecoveryReport | None:
        try:
            with self.factory() as db:
                return recover_stale_work(db, self.settings)
        except Exception as exc:  # a failed sweep must never take the server down; the next one retries
            logger.error("recovery sweep failed: %s", type(exc).__name__, exc_info=exc)
            return None

    def start(self) -> None:
        self.sweep()
        if self.settings.recovery_interval_seconds > 0:
            self._thread = threading.Thread(target=self._loop, name="traceforge-recovery", daemon=True)
            self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.settings.recovery_interval_seconds):
            self.sweep()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
