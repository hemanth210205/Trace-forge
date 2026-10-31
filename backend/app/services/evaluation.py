"""Evaluation runs: creation, result submission, scoring and the run state machine.

pending -> running        first result submitted
pending|running -> scoring   finish requested (run row locked; no more submissions)
scoring -> completed       every case has a result and scoring succeeded
scoring -> incomplete      scoring succeeded but some cases never received a result
scoring -> failed          an evaluator raised (all score writes of the attempt are rolled back)
pending|running|scoring -> failed   aborted

The same rules are enforced by database triggers (migration 0003), so a bug here cannot write results to a
terminal run or skip a state.
"""

import logging
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.base import utcnow
from app.models import Dataset, DatasetVersion, DatasetVersionCase, EvaluationResult, EvaluationRun, Span, Trace
from app.services import evaluators as ev

logger = logging.getLogger("traceforge.evaluation")

ACCEPTING = ("pending", "running")
TERMINAL = ("completed", "incomplete", "failed")


def get_run(db: Session, project_id: uuid.UUID, run_id: uuid.UUID, *, lock: bool = False) -> EvaluationRun:
    stmt = select(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.project_id == project_id)
    run = db.scalar(stmt.with_for_update() if lock else stmt)
    if run is None:
        raise NotFound("evaluation_run")
    return run


def validate_evaluators(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        configs = ev.parse_configs(raw)
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        errors = getattr(exc, "errors", None)
        details = (
            [{"loc": list(e["loc"]), "msg": e["msg"]} for e in errors()] if callable(errors) else [{"msg": str(exc)}]
        )
        raise AppError(422, "invalid_evaluators", "The evaluator configuration is invalid.", details) from None
    return [c.model_dump(exclude_none=True) for c in configs]


def create_run(
    db: Session, project_id: uuid.UUID, version: DatasetVersion, name: str, evaluators: list[dict[str, Any]],
    user_id: uuid.UUID | None,
) -> EvaluationRun:
    run = EvaluationRun(
        project_id=project_id,
        dataset_version_id=version.id,
        name=name.strip() or "evaluation",
        evaluators=validate_evaluators(evaluators),
        total_cases=version.case_count,
        created_by=user_id,
    )
    db.add(run)
    db.flush()
    return run


def submit_results(db: Session, run: EvaluationRun, items: list[Any]) -> int:
    """Upserts results for cases of the run's dataset version. Caller holds the run row lock."""
    if run.status not in ACCEPTING:
        raise AppError(409, "run_not_accepting_results", f"The run is {run.status}; results can no longer be submitted.")
    case_ids = set(db.scalars(select(DatasetVersionCase.id).where(DatasetVersionCase.version_id == run.dataset_version_id)))
    unknown = [str(i.case_id) for i in items if i.case_id not in case_ids]
    if unknown:
        raise AppError(422, "unknown_cases", "Some case ids do not belong to this run's dataset version.", unknown[:50])
    if run.status == "pending":
        run.status = "running"
        run.started_at = utcnow()
        db.flush()
    existing = {
        r.case_id: r
        for r in db.scalars(
            select(EvaluationResult).where(
                EvaluationResult.run_id == run.id, EvaluationResult.case_id.in_([i.case_id for i in items])
            )
        )
    }
    for item in {i.case_id: i for i in items}.values():  # last submission for a case wins
        result = existing.get(item.case_id) or EvaluationResult(run_id=run.id, case_id=item.case_id)
        result.output = item.output
        result.execution_error = item.error
        result.trace_id = item.trace_id
        result.latency_ms = item.latency_ms
        result.submitted_at = utcnow()
        db.add(result)
    db.flush()
    return len(items)


def received_count(db: Session, run_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(EvaluationResult).where(EvaluationResult.run_id == run_id)) or 0


def begin_scoring(db: Session, run: EvaluationRun) -> None:
    """pending|running -> scoring. Caller holds the run row lock and commits."""
    if run.status not in ACCEPTING:
        raise AppError(409, "run_not_finishable", f"The run is {run.status}; only pending or running runs can be finished.")
    run.status = "scoring"
    run.scoring_started_at = utcnow()
    if run.started_at is None:
        run.started_at = run.scoring_started_at


def finish_run(db: Session, project_id: uuid.UUID, run_id: uuid.UUID) -> EvaluationRun:
    run = get_run(db, project_id, run_id, lock=True)
    begin_scoring(db, run)
    db.commit()
    try:
        _score(db, run_id)
        db.commit()
    except Exception as exc:  # any scoring exception fails the run; partial score writes are discarded
        db.rollback()
        logger.error("scoring failed for evaluation run %s: %s", run_id, type(exc).__name__, exc_info=exc)
        run = get_run(db, project_id, run_id, lock=True)
        if run.status == "scoring":
            _mark_failed(db, run, f"Scoring error: {type(exc).__name__}: {str(exc)[:500]}")
            db.commit()
    db.expire_all()
    return get_run(db, project_id, run_id)


def abort_run(db: Session, project_id: uuid.UUID, run_id: uuid.UUID, reason: str) -> EvaluationRun:
    run = get_run(db, project_id, run_id, lock=True)
    if run.status in TERMINAL:
        raise AppError(409, "run_already_finished", f"The run is already {run.status}.")
    _mark_failed(db, run, reason)
    db.flush()
    return run


def _mark_failed(db: Session, run: EvaluationRun, reason: str) -> None:
    # Read everything first: a query after the status change would autoflush "failed", and the database then
    # (correctly) rejects any further write to the now-terminal run.
    summary = {"received_cases": received_count(db, run.id), "total_cases": run.total_cases}
    run.summary = summary
    run.failure_reason = reason
    run.finished_at = utcnow()
    run.status = "failed"


def _evidence(db: Session, project_id: uuid.UUID, trace_ids: set[uuid.UUID]) -> dict[uuid.UUID, ev.Evidence]:
    """Evidence for traces that really exist in the run's project; unknown ids get none (no fabrication)."""
    if not trace_ids:
        return {}
    found = set(db.scalars(select(Trace.id).where(Trace.project_id == project_id, Trace.id.in_(trace_ids))))
    evidence = {tid: ev.Evidence(trace_id=str(tid)) for tid in found}
    if found:
        spans = db.execute(
            select(Span.trace_id, Span.id, Span.kind, Span.status, Span.attributes)
            .where(Span.project_id == project_id, Span.trace_id.in_(found))
            .order_by(Span.started_at)
        )
        for trace_id, span_id, kind, status, attributes in spans:
            item = evidence[trace_id]
            if status == "error":
                item.error_span_ids.append(str(span_id))
            documents = (attributes or {}).get("documents")
            if kind == "retrieval" and isinstance(documents, list):
                item.retrieval_spans.append(
                    ev.RetrievalSpan(str(span_id), [d for d in documents if isinstance(d, dict)])
                )
    return evidence


def _score(db: Session, run_id: uuid.UUID) -> None:
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
    if run is None or run.status != "scoring":
        raise AppError(409, "run_not_scoring", "The run is not in the scoring state.")
    configs = ev.parse_configs(run.evaluators)
    cases = {c.id: c for c in db.scalars(select(DatasetVersionCase).where(DatasetVersionCase.version_id == run.dataset_version_id))}
    results = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run.id)))
    evidence = _evidence(db, run.project_id, {r.trace_id for r in results if r.trace_id})

    per_evaluator: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "not_applicable": 0})
    counts = {"passed": 0, "failed": 0, "not_evaluated": 0, "execution_errors": 0}
    score_sum, scored = Decimal(0), 0
    now = utcnow()
    for result in results:
        case = cases[result.case_id]
        proof = evidence.get(result.trace_id) if result.trace_id else None
        outcomes = [
            ev.evaluate(
                config,
                ev.CaseData(case.expected_output, case.expected_sources),
                ev.ResultData(result.output, result.execution_error),
                proof or ev.Evidence(),
            )
            for config in configs
        ]
        applicable = [o for o in outcomes if o["applicable"]]
        for o in outcomes:
            bucket = per_evaluator[o["evaluator"]]
            bucket["not_applicable" if not o["applicable"] else "passed" if o["passed"] else "failed"] += 1
        result.trace_found = None if result.trace_id is None else proof is not None
        result.evaluations = outcomes
        result.scored_at = now
        if result.execution_error is not None:
            counts["execution_errors"] += 1
        if applicable:
            result.passed = all(o["passed"] for o in applicable)
            result.score = Decimal(str(round(sum(o["score"] for o in applicable) / len(applicable), 4)))
            counts["passed" if result.passed else "failed"] += 1
            score_sum += result.score
            scored += 1
        else:
            result.passed, result.score = None, None
            counts["not_evaluated"] += 1
    db.flush()

    missing = run.total_cases - len(results)
    run.summary = {
        "total_cases": run.total_cases,
        "received_cases": len(results),
        "missing_cases": missing,
        **counts,
        "mean_score": float(round(score_sum / scored, 4)) if scored else None,
        "evaluators": {
            name: {**bucket, "pass_rate": round(bucket["passed"] / (bucket["passed"] + bucket["failed"]), 4)
                   if bucket["passed"] + bucket["failed"] else None}
            for name, bucket in per_evaluator.items()
        },
    }
    run.status = "incomplete" if missing > 0 else "completed"
    run.finished_at = now
    db.flush()


def pass_rate(run: EvaluationRun) -> float | None:
    """Exposed only for completed runs: an incomplete or failed run has no trustworthy pass rate."""
    if run.status != "completed":
        return None
    passed, failed = run.summary.get("passed", 0), run.summary.get("failed", 0)
    return round(passed / (passed + failed), 4) if passed + failed else None


def run_out(db: Session, run: EvaluationRun) -> dict[str, Any]:
    version, dataset = db.execute(
        select(DatasetVersion, Dataset).join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.id == run.dataset_version_id)
    ).one()
    return {
        "id": run.id,
        "project_id": run.project_id,
        "dataset_id": dataset.id,
        "dataset_name": dataset.name,
        "dataset_version_id": version.id,
        "dataset_version": version.version,
        "name": run.name,
        "status": run.status,
        "evaluators": run.evaluators,
        "total_cases": run.total_cases,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "scoring_started_at": run.scoring_started_at,
        "finished_at": run.finished_at,
        "failure_reason": run.failure_reason,
        "received_cases": received_count(db, run.id),
        "summary": run.summary,
        "pass_rate": pass_rate(run),
    }

