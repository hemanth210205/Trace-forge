"""Controlled experiments: definition, execution states, comparison, human decisions, hypothesis validation.

pending -> running      the executor starts the experiment (deadline set)
running -> completed    both arm runs are scored (completed or incomplete); the comparison is stored
running -> failed       an arm run failed, the deadline passed, or the experiment was aborted
pending -> failed       aborted before it started

The comparison only reports what was measured: pass-rate deltas need both arms ``completed``; latency, token
and cost comparisons only use cases measured in *both* arms and say so when there are none.
"""

import json
import statistics
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, NotFound
from app.db.base import utcnow
from app.models import (
    DatasetVersion,
    DatasetVersionCase,
    Diagnosis,
    DiagnosisCase,
    DiagnosisHypothesis,
    EvaluationResult,
    EvaluationRun,
    Experiment,
    ExperimentDecision,
    Trace,
)
from app.services import evaluation as runs
from app.services.audit import record_audit

MAX_CONFIG_BYTES = 16 * 1024
ARMS = ("baseline", "candidate")


# --- definition ------------------------------------------------------------------------------------------------


def diff_configs(baseline: Any, candidate: Any, path: str = "") -> list[dict[str, Any]]:
    """Every leaf that differs, as {path, change, baseline, candidate}. Lists are compared as whole values."""
    if isinstance(baseline, dict) and isinstance(candidate, dict):
        out: list[dict[str, Any]] = []
        for key in sorted(baseline.keys() | candidate.keys()):
            sub = f"{path}.{key}" if path else key
            if key not in candidate:
                out.append({"path": sub, "change": "removed", "baseline": baseline[key], "candidate": None})
            elif key not in baseline:
                out.append({"path": sub, "change": "added", "baseline": None, "candidate": candidate[key]})
            else:
                out += diff_configs(baseline[key], candidate[key], sub)
        return out
    if baseline != candidate or type(baseline) is not type(candidate):
        return [{"path": path or "(root)", "change": "changed", "baseline": baseline, "candidate": candidate}]
    return []


def get_experiment(db: Session, project_id: uuid.UUID, experiment_id: uuid.UUID, *, lock: bool = False) -> Experiment:
    stmt = select(Experiment).where(Experiment.id == experiment_id, Experiment.project_id == project_id)
    experiment = db.scalar(stmt.with_for_update() if lock else stmt)
    if experiment is None:
        raise NotFound("experiment")
    return experiment


def _check_config(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(422, "invalid_config", f"The {name} configuration must be a JSON object.")
    if len(json.dumps(value, default=str).encode()) > MAX_CONFIG_BYTES:
        raise AppError(422, "invalid_config", f"The {name} configuration exceeds {MAX_CONFIG_BYTES} bytes.")
    return value


def create_experiment(
    db: Session, *, project_id: uuid.UUID, source_run_id: uuid.UUID, name: str, description: str,
    baseline_config: Any, candidate_config: Any, hypothesis_id: uuid.UUID | None, user_id: uuid.UUID, max_cases: int,
) -> Experiment:
    source = runs.get_run(db, project_id, source_run_id)
    if source.status != "completed":
        raise AppError(409, "source_run_not_completed", f"Experiments start from a completed evaluation run; this one is {source.status}.")
    version = db.get(DatasetVersion, source.dataset_version_id)
    assert version is not None
    if version.case_count > max_cases:
        raise AppError(422, "too_many_cases", f"The dataset version has {version.case_count} cases; experiments are limited to {max_cases}.")
    baseline, candidate = _check_config("baseline", baseline_config), _check_config("candidate", candidate_config)
    diff = diff_configs(baseline, candidate)
    if not diff:
        raise AppError(422, "configs_identical", "The baseline and candidate configurations are identical; there is nothing to compare.")
    if hypothesis_id is not None:
        owner = db.execute(
            select(Diagnosis.project_id, Diagnosis.run_id).join(DiagnosisHypothesis, DiagnosisHypothesis.diagnosis_id == Diagnosis.id)
            .where(DiagnosisHypothesis.id == hypothesis_id)
        ).first()
        if owner is None or owner.project_id != project_id:
            raise NotFound("hypothesis")
        if owner.run_id != source.id:
            raise AppError(422, "hypothesis_from_other_run", "The hypothesis must come from a diagnosis of the source run.")
    arms = {arm: runs.create_run(db, project_id, version, f"{name} · {arm}"[:200], source.evaluators, user_id) for arm in ARMS}
    experiment = Experiment(
        project_id=project_id, source_run_id=source.id, dataset_version_id=version.id,
        baseline_run_id=arms["baseline"].id, candidate_run_id=arms["candidate"].id, hypothesis_id=hypothesis_id,
        name=name, description=description, baseline_config=baseline, candidate_config=candidate, config_diff=diff,
        created_by=user_id,
    )
    db.add(experiment)
    db.flush()
    return experiment


# --- execution states -----------------------------------------------------------------------------------------


def start(db: Session, experiment: Experiment, time_limit_seconds: float) -> None:
    """pending -> running. Idempotent for a running experiment. Caller holds the row lock."""
    if experiment.status == "running":
        return
    if experiment.status != "pending":
        raise AppError(409, "experiment_not_startable", f"The experiment is {experiment.status}.")
    now = utcnow()
    experiment.started_at = now
    experiment.deadline_at = now + timedelta(seconds=time_limit_seconds)
    experiment.status = "running"


def _fail(experiment: Experiment, reason: str) -> None:
    experiment.failure_reason = reason[:1000]
    experiment.finished_at = utcnow()
    experiment.status = "failed"


def abort(db: Session, experiment: Experiment, reason: str) -> None:
    if experiment.status not in ("pending", "running"):
        raise AppError(409, "experiment_already_finished", f"The experiment is already {experiment.status}.")
    for run_id in (experiment.baseline_run_id, experiment.candidate_run_id):
        run = db.get(EvaluationRun, run_id)
        if run is not None and run.status in ("pending", "running", "scoring"):
            runs.abort_run(db, experiment.project_id, run_id, "Experiment aborted.")
    _fail(experiment, reason)


def complete(db: Session, experiment: Experiment) -> None:
    """running -> completed (with the comparison) or failed. Caller holds the experiment row lock."""
    if experiment.status != "running":
        raise AppError(409, "experiment_not_running", f"Only a running experiment can be completed; this one is {experiment.status}.")
    if experiment.deadline_at is not None and utcnow() > experiment.deadline_at:
        _fail(experiment, "The experiment exceeded its time limit before both arms were scored.")
        return
    arm_runs = {arm: db.get(EvaluationRun, getattr(experiment, f"{arm}_run_id")) for arm in ARMS}
    unfinished = [arm for arm, run in arm_runs.items() if run is None or run.status in runs.ACCEPTING + ("scoring",)]
    if unfinished:
        raise AppError(409, "arms_not_finished", f"Arm(s) not scored yet: {', '.join(unfinished)}. Finish their runs first.")
    failed = [arm for arm, run in arm_runs.items() if run.status == "failed"]  # type: ignore[union-attr]
    if failed:
        _fail(experiment, f"The {' and '.join(failed)} arm run failed; no comparison is possible.")
        return
    base_run, cand_run = arm_runs["baseline"], arm_runs["candidate"]
    assert base_run is not None and cand_run is not None
    experiment.comparison = compute_comparison(
        arm_case_rows(db, experiment.project_id, base_run), arm_case_rows(db, experiment.project_id, cand_run), base_run, cand_run
    )
    experiment.finished_at = utcnow()
    experiment.status = "completed"


# --- comparison ------------------------------------------------------------------------------------------------


def arm_case_rows(db: Session, project_id: uuid.UUID, run: EvaluationRun) -> dict[str, dict[str, Any]]:
    """Per case of the run's version: state, score, evaluator outcomes, latency, and tokens/cost of the linked trace."""
    rows = db.execute(
        select(DatasetVersionCase, EvaluationResult)
        .outerjoin(EvaluationResult, (EvaluationResult.case_id == DatasetVersionCase.id) & (EvaluationResult.run_id == run.id))
        .where(DatasetVersionCase.version_id == run.dataset_version_id).order_by(DatasetVersionCase.position)
    ).all()
    trace_ids = {r.trace_id for _, r in rows if r is not None and r.trace_id}
    traces = {t.id: t for t in db.scalars(select(Trace).where(Trace.project_id == project_id, Trace.id.in_(trace_ids)))} if trace_ids else {}
    out: dict[str, dict[str, Any]] = {}
    for case, result in rows:
        trace = traces.get(result.trace_id) if result and result.trace_id else None
        if result is None:
            state = "missing"
        else:
            state = "not_evaluated" if result.passed is None else "passed" if result.passed else "failed"
        out[case.case_key] = {
            "case_id": str(case.id), "position": case.position, "input": case.input, "expected_output": case.expected_output,
            "state": state, "score": float(result.score) if result and result.score is not None else None,
            "output": result.output if result else None, "execution_error": result.execution_error if result else None,
            "evaluations": result.evaluations if result else [],
            "trace_id": str(trace.id) if trace else None,
            "latency_ms": result.latency_ms if result else None,
            # Only traces that reported usage count as measured; zero from a trace with no LLM spans is unknown.
            "tokens": trace.total_tokens if trace is not None and trace.total_tokens > 0 else None,
            "cost_usd": float(trace.total_cost_usd) if trace is not None and trace.total_cost_usd > Decimal(0) else None,
        }
    return out


def _arm_summary(run: EvaluationRun, cases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    states = [c["state"] for c in cases.values()]
    evaluators: dict[str, dict[str, int]] = {}
    recalls: list[float] = []
    for c in cases.values():
        for o in c["evaluations"]:
            if not o.get("applicable"):
                continue
            bucket = evaluators.setdefault(o["evaluator"], {"passed": 0, "failed": 0})
            bucket["passed" if o.get("passed") else "failed"] += 1
            if o.get("type") == "source_match" and o.get("score") is not None:
                recalls.append(float(o["score"]))
    passed, failed = states.count("passed"), states.count("failed")
    latencies = [c["latency_ms"] for c in cases.values() if c["latency_ms"] is not None]
    tokens = [c["tokens"] for c in cases.values() if c["tokens"] is not None]
    costs = [c["cost_usd"] for c in cases.values() if c["cost_usd"] is not None]
    return {
        "run_id": str(run.id), "status": run.status, "total_cases": len(cases), "passed": passed, "failed": failed,
        "missing": states.count("missing"), "not_evaluated": states.count("not_evaluated"),
        "pass_rate": round(passed / (passed + failed), 4) if run.status == "completed" and passed + failed else None,
        "evaluators": {k: {**v, "pass_rate": round(v["passed"] / (v["passed"] + v["failed"]), 4) if v["passed"] + v["failed"] else None}
                       for k, v in sorted(evaluators.items())},
        "retrieval": {"checked_cases": len(recalls), "mean_recall": round(statistics.fmean(recalls), 4) if recalls else None},
        "latency_ms": {"measured_cases": len(latencies), "mean": round(statistics.fmean(latencies), 3) if latencies else None,
                       "median": round(statistics.median(latencies), 3) if latencies else None},
        "tokens": {"measured_cases": len(tokens), "total": sum(tokens) if tokens else None},
        "cost_usd": {"measured_cases": len(costs), "total": round(sum(costs), 6) if costs else None},
    }


def _paired(base: dict[str, dict[str, Any]], cand: dict[str, dict[str, Any]], field: str) -> dict[str, Any]:
    keys = [k for k in base if base[k][field] is not None and cand.get(k, {}).get(field) is not None]
    if not keys:
        return {"paired_cases": 0, "available": False, "baseline_mean": None, "candidate_mean": None, "delta": None}
    b = statistics.fmean(base[k][field] for k in keys)
    c = statistics.fmean(cand[k][field] for k in keys)
    return {"paired_cases": len(keys), "available": True, "baseline_mean": round(b, 6), "candidate_mean": round(c, 6), "delta": round(c - b, 6)}


def case_change(b: str, c: str) -> str:
    if b == "failed" and c == "passed":
        return "fixed"
    if b == "passed" and c == "failed":
        return "regressed"
    if b == c == "passed":
        return "unchanged_pass"
    if b == c == "failed":
        return "unchanged_fail"
    return "incomplete"


def evaluator_changes(base: dict[str, Any], cand: dict[str, Any]) -> list[dict[str, Any]]:
    b = {o["evaluator"]: o for o in base["evaluations"] if o.get("applicable")}
    c = {o["evaluator"]: o for o in cand["evaluations"] if o.get("applicable")}
    out = []
    for name in sorted(b.keys() & c.keys()):
        if bool(b[name].get("passed")) != bool(c[name].get("passed")):
            out.append({"evaluator": name, "change": "fixed" if c[name].get("passed") else "regressed"})
    return out


def compute_comparison(base: dict[str, dict[str, Any]], cand: dict[str, dict[str, Any]],
                       base_run: EvaluationRun, cand_run: EvaluationRun) -> dict[str, Any]:
    arms = {"baseline": _arm_summary(base_run, base), "candidate": _arm_summary(cand_run, cand)}
    groups: dict[str, list[str]] = {"fixed": [], "regressed": [], "unchanged_pass": [], "unchanged_fail": [], "incomplete": []}
    ev_changes = []
    for key in base:
        groups[case_change(base[key]["state"], cand.get(key, {}).get("state", "missing"))].append(key)
        ev_changes += [{"case": key, **change} for change in evaluator_changes(base[key], cand.get(key, {"evaluations": []}))]
    both_completed = base_run.status == cand_run.status == "completed"
    pass_delta = (round(arms["candidate"]["pass_rate"] - arms["baseline"]["pass_rate"], 4)
                  if both_completed and arms["baseline"]["pass_rate"] is not None and arms["candidate"]["pass_rate"] is not None else None)
    measurements = {field: _paired(base, cand, field) for field in ("latency_ms", "tokens", "cost_usd")}
    fixed, regressed = len(groups["fixed"]), len(groups["regressed"])
    if not both_completed:
        outcome, reason = "inconclusive", "At least one arm is incomplete, so the arms are not comparable on every case."
    elif regressed and fixed:
        outcome, reason = "mixed", f"{fixed} case(s) fixed and {regressed} regressed."
    elif regressed:
        outcome, reason = "candidate_regresses", f"{regressed} case(s) regressed and none were fixed."
    elif fixed:
        outcome, reason = "candidate_improves", f"{fixed} case(s) fixed with no regressions."
    else:
        outcome, reason = "no_change", "No case changed outcome."
    return {
        "arms": arms,
        "pass_rate_delta": pass_delta,
        "cases": groups,
        "evaluator_changes": ev_changes,
        "measurements": measurements,
        "suggested_outcome": outcome,
        "suggested_outcome_reason": reason + " This is a computed suggestion; promotion requires a human decision.",
    }


# --- decisions and hypothesis validation --------------------------------------------------------------------------


def record_decision(db: Session, experiment: Experiment, decision: str, rationale: str, user_id: uuid.UUID) -> ExperimentDecision:
    if experiment.status != "completed":
        raise AppError(409, "experiment_not_completed", "Decisions can only be recorded on completed experiments.")
    row = ExperimentDecision(experiment_id=experiment.id, decision=decision, rationale=rationale.strip(), decided_by=user_id)
    db.add(row)
    record_audit(db, action=f"experiment.{'approved' if decision == 'approve' else 'rejected'}", target_type="experiment",
                 target_id=experiment.id, actor_user_id=user_id, project_id=experiment.project_id,
                 details={"name": experiment.name, "config_diff_paths": [d["path"] for d in experiment.config_diff][:20]})
    db.flush()
    return row


def hypothesis_case_key(db: Session, hypothesis_id: uuid.UUID) -> tuple[DiagnosisHypothesis, str] | None:
    row = db.execute(
        select(DiagnosisHypothesis, DiagnosisCase.case_key).join(DiagnosisCase, DiagnosisCase.id == DiagnosisHypothesis.case_id)
        .where(DiagnosisHypothesis.id == hypothesis_id)
    ).first()
    return (row[0], row[1]) if row else None


def validation_eligibility(experiment: Experiment, case_key: str) -> tuple[bool, str]:
    """A hypothesis is validated only if the baseline arm reproduced the failure and the candidate fixed it."""
    if experiment.status != "completed":
        return False, "The experiment has not completed."
    cases = experiment.comparison.get("cases", {})
    if case_key in cases.get("fixed", []):
        return True, f"Baseline reproduced the failure of {case_key} and the candidate fixed it."
    if case_key in cases.get("unchanged_fail", []):
        return False, f"{case_key} still fails with the candidate configuration."
    if case_key in cases.get("unchanged_pass", []):
        return False, f"The baseline arm did not reproduce the failure of {case_key}."
    return False, f"{case_key} was not evaluated in both arms."


def validate_hypothesis(db: Session, experiment: Experiment, user_id: uuid.UUID) -> DiagnosisHypothesis:
    if experiment.hypothesis_id is None:
        raise AppError(409, "no_hypothesis", "This experiment does not test a diagnosis hypothesis.")
    found = hypothesis_case_key(db, experiment.hypothesis_id)
    if found is None:
        raise AppError(409, "no_hypothesis", "The linked hypothesis no longer exists.")
    hypothesis, case_key = found
    if hypothesis.validation_status == "validated":
        raise AppError(409, "already_validated", f"The hypothesis is already validated by {hypothesis.validated_by}.")
    eligible, reason = validation_eligibility(experiment, case_key)
    if not eligible:
        raise AppError(409, "not_validated_by_experiment", reason)
    hypothesis.validation_status = "validated"
    hypothesis.validated_by = f"experiment:{experiment.id}"
    record_audit(db, action="hypothesis.validated", target_type="diagnosis_hypothesis", target_id=hypothesis.id,
                 actor_user_id=user_id, project_id=experiment.project_id,
                 details={"experiment_id": str(experiment.id), "case": case_key, "category": hypothesis.category})
    db.flush()
    return hypothesis
