"""Controlled experiments: comparison logic, API flow, database rules, decisions, hypothesis validation."""

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import AuditLog, DiagnosisHypothesis, EvaluationRun, Experiment, ExperimentDecision
from app.services.experiments import compute_comparison, diff_configs
from tests.conftest import ApiClient
from tests.test_evaluation_runs import Env

EVALUATORS = [{"type": "normalized_match", "field": "answer"}, {"type": "source_match"}]
BASELINE, CANDIDATE = {"retriever": "keyword", "k": 2}, {"retriever": "stemmed", "k": 2}
GOOD = {"shipping": "Your order is shipped.", "refunds": "Refunds take 5 days.", "warranty": "12 months."}


# --- pure comparison logic ---------------------------------------------------------------------------------------


def row(state: str, *, latency: float | None = 10.0, tokens: int | None = None, cost: float | None = None,
        evaluations: list[dict] | None = None) -> dict[str, Any]:
    return {"state": state, "latency_ms": latency, "tokens": tokens, "cost_usd": cost, "evaluations": evaluations or []}


def ev(name: str, passed: bool, score: float | None = None, type_: str | None = None) -> dict[str, Any]:
    return {"evaluator": name, "type": type_ or name, "applicable": True, "passed": passed, "score": score if score is not None else float(passed)}


def run(status: str = "completed") -> Any:
    return SimpleNamespace(id=uuid.uuid4(), status=status)


def test_config_diff_lists_every_changed_leaf() -> None:
    diff = diff_configs({"retriever": {"type": "kw", "k": 2}, "model": "a", "old": 1}, {"retriever": {"type": "stem", "k": 2}, "model": "a", "new": True})
    assert diff == [
        {"path": "new", "change": "added", "baseline": None, "candidate": True},
        {"path": "old", "change": "removed", "baseline": 1, "candidate": None},
        {"path": "retriever.type", "change": "changed", "baseline": "kw", "candidate": "stem"},
    ]
    assert diff_configs({"a": 1}, {"a": 1}) == [] and diff_configs({"a": 1}, {"a": True}) != []


def test_fixes_regressions_and_evaluator_changes() -> None:
    base = {"a": row("failed", evaluations=[ev("src", False, 0.0, "source_match")]), "b": row("passed", evaluations=[ev("src", True, 1.0, "source_match")]),
            "c": row("failed"), "d": row("passed")}
    cand = {"a": row("passed", evaluations=[ev("src", True, 1.0, "source_match")]), "b": row("failed", evaluations=[ev("src", False, 0.5, "source_match")]),
            "c": row("failed"), "d": row("passed")}
    out = compute_comparison(base, cand, run(), run())
    assert out["cases"] == {"fixed": ["a"], "regressed": ["b"], "unchanged_pass": ["d"], "unchanged_fail": ["c"], "incomplete": []}
    assert out["evaluator_changes"] == [{"case": "a", "evaluator": "src", "change": "fixed"}, {"case": "b", "evaluator": "src", "change": "regressed"}]
    assert out["suggested_outcome"] == "mixed" and "requires a human decision" in out["suggested_outcome_reason"]
    assert out["arms"]["baseline"]["pass_rate"] == 0.5 and out["pass_rate_delta"] == 0.0
    assert out["arms"]["candidate"]["retrieval"] == {"checked_cases": 2, "mean_recall": 0.75}


@pytest.mark.parametrize(
    ("base", "cand", "outcome"),
    [(["failed", "passed"], ["passed", "passed"], "candidate_improves"), (["passed"], ["failed"], "candidate_regresses"),
     (["passed"], ["passed"], "no_change")],
)
def test_suggested_outcomes(base: list[str], cand: list[str], outcome: str) -> None:
    b = {str(i): row(s) for i, s in enumerate(base)}
    c = {str(i): row(s) for i, s in enumerate(cand)}
    assert compute_comparison(b, c, run(), run())["suggested_outcome"] == outcome


def test_incomplete_arm_withholds_pass_rate_delta() -> None:
    out = compute_comparison({"a": row("passed"), "b": row("passed")}, {"a": row("passed"), "b": row("missing")}, run(), run("incomplete"))
    assert out["pass_rate_delta"] is None and out["arms"]["candidate"]["pass_rate"] is None
    assert out["suggested_outcome"] == "inconclusive" and out["cases"]["incomplete"] == ["b"]


def test_measurements_are_only_compared_on_cases_measured_in_both_arms() -> None:
    base = {"a": row("passed", latency=10, tokens=100, cost=None), "b": row("passed", latency=None, tokens=50, cost=None)}
    cand = {"a": row("passed", latency=30, tokens=None, cost=None), "b": row("passed", latency=5, tokens=70, cost=None)}
    m = compute_comparison(base, cand, run(), run())["measurements"]
    assert m["latency_ms"] == {"paired_cases": 1, "available": True, "baseline_mean": 10, "candidate_mean": 30, "delta": 20}
    assert m["tokens"]["paired_cases"] == 1 and m["tokens"]["delta"] == 20
    assert m["cost_usd"] == {"paired_cases": 0, "available": False, "baseline_mean": None, "candidate_mean": None, "delta": None}


# --- API and database ----------------------------------------------------------------------------------------------


@pytest.fixture
def env(alice: ApiClient, client: ApiClient) -> Env:
    e = Env(alice, client)
    source = e.create_run(EVALUATORS)
    ids = e.executor_cases(source)
    e.submit(source, [{"case_id": ids[k], "output": {"answer": GOOD[k] if k != "warranty" else "Two years."},
                       "trace_id": e.trace([{"shipping": "kb/x.md", "refunds": "kb/returns.md", "warranty": "kb/w.md"}[k]])} for k in ids])
    assert e.finish(source).json()["status"] == "completed"
    e.source = source  # type: ignore[attr-defined]
    return e


def create(env: Env, **overrides: Any) -> Any:
    body = {"source_run_id": env.source["id"], "name": "stemmed retriever", "baseline_config": BASELINE, "candidate_config": CANDIDATE, **overrides}  # type: ignore[attr-defined]
    return env.alice.post(f"{env.base}/experiments", json=body)


def headers(env: Env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env.key}"}


def execute_arm(env: Env, run_id: str, answers: dict[str, str], sources: dict[str, list[str]]) -> None:
    run = {"id": run_id}
    ids = env.executor_cases(run)
    env.submit(run, [{"case_id": ids[k], "output": {"answer": answers[k]}, "trace_id": env.trace(sources[k]), "latency_ms": 12.5}
                     for k in ids])
    assert env.finish(run).json()["status"] == "completed"


SRC_BASE = {"shipping": ["kb/x.md"], "refunds": ["kb/returns.md"], "warranty": ["kb/w.md"]}
SRC_CAND = {"shipping": ["kb/shipping.md"], "refunds": ["kb/returns.md"], "warranty": ["kb/w.md"]}


def run_experiment(env: Env, exp: dict[str, Any], cand_answers: dict[str, str] | None = None) -> Any:
    assert env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers=headers(env)).json()["status"] == "running"
    execute_arm(env, exp["baseline"]["run_id"], {**GOOD, "warranty": "Two years."}, SRC_BASE)
    execute_arm(env, exp["candidate"]["run_id"], cand_answers or {**GOOD, "warranty": "Two years."}, SRC_CAND)
    return env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/complete", headers=headers(env))


def test_create_builds_two_pending_arms_on_the_exact_dataset_version(env: Env, db: Session) -> None:
    response = create(env, description="Does stemming fix the retrieval miss?")
    assert response.status_code == 201
    exp = response.json()
    assert exp["status"] == "pending" and exp["dataset_version_id"] == env.version["id"]
    assert exp["config_diff"] == [{"path": "retriever", "change": "changed", "baseline": "keyword", "candidate": "stemmed"}]
    for arm, config in (("baseline", BASELINE), ("candidate", CANDIDATE)):
        assert exp[arm]["config"] == config and exp[arm]["run_status"] == "pending" and exp[arm]["total_cases"] == 3
        arm_run = db.get(EvaluationRun, uuid.UUID(exp[arm]["run_id"]))
        assert arm_run.dataset_version_id == uuid.UUID(env.version["id"]) and arm_run.evaluators == env.source["evaluators"]  # type: ignore[attr-defined]
    assert exp["hypothesis"] is None and exp["comparison"] == {} and exp["decisions"] == []


@pytest.mark.parametrize(
    ("overrides", "code"),
    [({"candidate_config": BASELINE}, "configs_identical"), ({"baseline_config": {"blob": "x" * 17000}}, "invalid_config")],
)
def test_invalid_definitions_are_rejected(env: Env, overrides: dict, code: str) -> None:
    response = create(env, **overrides)
    assert response.status_code == 422 and response.json()["error"]["code"] == code


def test_source_run_must_be_completed(env: Env) -> None:
    pending = env.create_run(EVALUATORS)
    response = create(env, source_run_id=pending["id"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "source_run_not_completed"


def test_full_flow_compares_arms_and_links_traces(env: Env) -> None:
    exp = create(env).json()
    early = env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers=headers(env))
    assert early.json()["status"] == "running"
    blocked = env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/complete", headers=headers(env))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "arms_not_finished"

    execute_arm(env, exp["baseline"]["run_id"], {**GOOD, "warranty": "Two years."}, SRC_BASE)
    execute_arm(env, exp["candidate"]["run_id"], {**GOOD, "refunds": "No refunds.", "warranty": "Two years."}, SRC_CAND)
    done = env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/complete", headers=headers(env)).json()
    assert done["status"] == "completed" and done["finished_at"]
    comparison = done["comparison"]
    assert comparison["cases"]["fixed"] == ["shipping"] and comparison["cases"]["regressed"] == ["refunds"]
    assert comparison["suggested_outcome"] == "mixed"
    assert comparison["measurements"]["latency_ms"]["paired_cases"] == 3
    assert comparison["measurements"]["cost_usd"]["available"] is False  # the arm traces report no cost

    cases = {c["case_key"]: c for c in env.alice.get(f"{env.base}/experiments/{exp['id']}/cases").json()}
    assert cases["shipping"]["change"] == "fixed"
    assert {"evaluator": "source_match", "change": "fixed"} in cases["shipping"]["evaluator_changes"]
    assert cases["refunds"]["change"] == "regressed" and cases["refunds"]["candidate"]["output"] == {"answer": "No refunds."}
    for c in cases.values():
        for arm in ("baseline", "candidate"):
            assert env.alice.get(f"{env.base}/traces/{c[arm]['trace_id']}").status_code == 200
    assert cases["shipping"]["baseline"]["trace_id"] != cases["shipping"]["candidate"]["trace_id"]


def test_a_failed_arm_fails_the_experiment(env: Env) -> None:
    exp = create(env).json()
    env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers=headers(env))
    execute_arm(env, exp["baseline"]["run_id"], {**GOOD, "warranty": "x"}, SRC_BASE)
    env.alice.post(f"{env.base}/eval-runs/{exp['candidate']['run_id']}/abort")
    done = env.alice.post(f"{env.base}/experiments/{exp['id']}/complete").json()
    assert done["status"] == "failed" and "candidate arm run failed" in done["failure_reason"] and done["comparison"] == {}


def test_deadline_bounds_execution(env: Env, db: Session) -> None:
    exp = create(env).json()
    env.client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers=headers(env))
    db.execute(text("UPDATE experiments SET deadline_at = now() - interval '1 minute' WHERE id = :i"), {"i": exp["id"]})
    db.commit()
    done = env.alice.post(f"{env.base}/experiments/{exp['id']}/complete").json()
    assert done["status"] == "failed" and "time limit" in done["failure_reason"]


def test_abort_fails_the_experiment_and_its_arms(env: Env) -> None:
    exp = create(env).json()
    done = env.alice.post(f"{env.base}/experiments/{exp['id']}/abort").json()
    assert done["status"] == "failed" and done["baseline"]["run_status"] == done["candidate"]["run_status"] == "failed"
    assert env.alice.post(f"{env.base}/experiments/{exp['id']}/abort").status_code == 409


def test_decisions_are_human_append_only_and_audited(env: Env, db: Session) -> None:
    exp = create(env).json()
    early = env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions", json={"decision": "approve", "rationale": "looks good"})
    assert early.status_code == 409 and early.json()["error"]["code"] == "experiment_not_completed"
    assert run_experiment(env, exp).json()["status"] == "completed"
    assert env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions", json={"decision": "approve", "rationale": "  "}).status_code == 422
    approved = env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions",
                              json={"decision": "approve", "rationale": "Fixes the shipping retrieval miss; no regressions."}).json()
    rejected = env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions",
                              json={"decision": "reject", "rationale": "Hold until more cases are added."}).json()
    assert [d["decision"] for d in rejected["decisions"]] == ["approve", "reject"] and rejected["decisions"][0]["decided_by"] == "Alice"
    assert approved["status"] == "completed"
    actions = db.scalars(select(AuditLog.action).where(AuditLog.target_type == "experiment")).all()
    assert sorted(actions) == ["experiment.approved", "experiment.rejected"]
    decision_id = db.scalar(select(ExperimentDecision.id).limit(1))
    for statement in ("UPDATE experiment_decisions SET decision = 'reject' WHERE id = :i", "DELETE FROM experiment_decisions WHERE id = :i"):
        with pytest.raises(DBAPIError, match="append-only"):
            db.execute(text(statement), {"i": decision_id})
        db.rollback()


def test_database_rules_for_experiments(env: Env, db: Session) -> None:
    exp = create(env).json()
    other_version = env.alice.post(f"{env.base}/datasets", json={"name": "Other"}).json()
    env.alice.post(f"{env.base}/datasets/{other_version['id']}/cases", json={"input": "x"})
    v2 = env.alice.post(f"{env.base}/datasets/{other_version['id']}/versions", json={}).json()
    foreign_run = env.alice.post(f"{env.base}/eval-runs", json={"dataset_version_id": v2["id"], "name": "x", "evaluators": EVALUATORS}).json()
    with pytest.raises(DBAPIError, match="must use the experiment's project and dataset version"):
        db.execute(text(
            "INSERT INTO experiments (id, project_id, source_run_id, dataset_version_id, baseline_run_id, candidate_run_id, name, "
            "baseline_config, candidate_config, config_diff) SELECT gen_random_uuid(), project_id, source_run_id, dataset_version_id, "
            ":foreign, gen_random_uuid(), 'x', CAST(:b AS jsonb), CAST(:c AS jsonb), '[]' FROM experiments WHERE id = :e"),
            {"foreign": foreign_run["id"], "e": exp["id"], "b": '{"a": 1}', "c": '{"a": 2}'})
    db.rollback()
    with pytest.raises(DBAPIError, match="definition is immutable"):
        db.execute(text("UPDATE experiments SET candidate_config = CAST(:c AS jsonb) WHERE id = :e"), {"e": exp["id"], "c": '{"retriever": "other"}'})
    db.rollback()
    with pytest.raises(DBAPIError, match="invalid experiment transition pending -> completed"):
        db.execute(text("UPDATE experiments SET status = 'completed', finished_at = now() WHERE id = :e"), {"e": exp["id"]})
    db.rollback()
    env.alice.post(f"{env.base}/experiments/{exp['id']}/abort")
    with pytest.raises(DBAPIError, match="finished experiments are immutable"):
        db.execute(text("UPDATE experiments SET name = 'x' WHERE id = :e"), {"e": exp["id"]})
    db.rollback()
    with pytest.raises(DBAPIError, match="only be recorded on completed experiments"):
        db.execute(text("INSERT INTO experiment_decisions (id, experiment_id, decision, rationale) VALUES (gen_random_uuid(), :e, 'approve', 'abc')"),
                   {"e": exp["id"]})
    db.rollback()


def _diagnose(env: Env) -> dict[str, Any]:
    started = env.alice.post(f"{env.base}/eval-runs/{env.source['id']}/diagnoses").json()  # type: ignore[attr-defined]
    return env.alice.get(f"{env.base}/diagnoses/{started['id']}").json()


def test_hypothesis_is_validated_only_when_the_experiment_fixes_its_case(env: Env, db: Session) -> None:
    diagnosis = _diagnose(env)
    by_case = {c["case_key"]: c["hypotheses"][0] for c in diagnosis["cases"]}
    shipping_h, warranty_h = by_case["shipping"], by_case["warranty"]

    fixing = create(env, hypothesis_id=shipping_h["id"]).json()
    assert fixing["hypothesis"]["case_key"] == "shipping" and fixing["hypothesis"]["recommended_experiment"]
    assert fixing["hypothesis_validation"]["eligible"] is False  # not run yet
    premature = env.alice.post(f"{env.base}/experiments/{fixing['id']}/validate-hypothesis")
    assert premature.status_code == 409
    run_experiment(env, fixing)
    shown = env.alice.get(f"{env.base}/experiments/{fixing['id']}").json()
    assert shown["hypothesis_validation"] == {"eligible": True, "reason": "Baseline reproduced the failure of shipping and the candidate fixed it."}
    validated = env.alice.post(f"{env.base}/experiments/{fixing['id']}/validate-hypothesis").json()
    assert validated["hypothesis"]["validation_status"] == "validated" and validated["hypothesis"]["validated_by"] == f"experiment:{fixing['id']}"
    hyp = db.get(DiagnosisHypothesis, uuid.UUID(shipping_h["id"]))
    assert hyp.validated_by == f"experiment:{fixing['id']}" and hyp.evidence_status == shipping_h["evidence_status"]  # nothing else changed
    assert env.alice.post(f"{env.base}/experiments/{fixing['id']}/validate-hypothesis").json()["error"]["code"] == "already_validated"

    not_fixing = create(env, name="warranty attempt", hypothesis_id=warranty_h["id"]).json()
    run_experiment(env, not_fixing)
    refused = env.alice.post(f"{env.base}/experiments/{not_fixing['id']}/validate-hypothesis")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_validated_by_experiment"
    assert "still fails with the candidate" in refused.json()["error"]["message"]
    assert db.get(DiagnosisHypothesis, uuid.UUID(warranty_h["id"])).validation_status == "unvalidated"


def test_hypothesis_must_belong_to_the_source_run_and_project(env: Env, bob: ApiClient) -> None:
    diagnosis = _diagnose(env)
    hypothesis_id = diagnosis["cases"][0]["hypotheses"][0]["id"]
    other_source = env.create_run(EVALUATORS)
    ids = env.executor_cases(other_source)
    env.submit(other_source, [{"case_id": i, "output": {"answer": "x"}} for i in ids.values()])
    env.finish(other_source)
    response = create(env, source_run_id=other_source["id"], hypothesis_id=hypothesis_id)
    assert response.status_code == 422 and response.json()["error"]["code"] == "hypothesis_from_other_run"
    assert create(env, hypothesis_id=str(uuid.uuid4())).status_code == 404


def test_experiments_are_isolated_and_writes_need_csrf(env: Env, bob: ApiClient, client: ApiClient) -> None:
    exp = create(env).json()
    other = env.alice.create_project("Other", slug="other")
    other_key = env.alice.create_api_key(other["id"])["key"]
    for response in (
        bob.get(f"{env.base}/experiments"), bob.get(f"{env.base}/experiments/{exp['id']}"),
        bob.post(f"{env.base}/experiments/{exp['id']}/abort"), bob.get(f"{env.base}/experiments/{exp['id']}/cases"),
        bob.post(f"{env.base}/experiments", json={"source_run_id": env.source["id"], "name": "x", "baseline_config": BASELINE, "candidate_config": CANDIDATE}),  # type: ignore[attr-defined]
        env.alice.get(f"/api/v1/projects/{other['id']}/experiments/{exp['id']}"),
        env.alice.post(f"/api/v1/projects/{other['id']}/experiments", json={"source_run_id": env.source["id"], "name": "x",  # type: ignore[attr-defined]
                                                                          "baseline_config": BASELINE, "candidate_config": CANDIDATE}),
        client.post(f"/api/v1/sdk/experiments/{exp['id']}/start", headers={"Authorization": f"Bearer {other_key}"}),
    ):
        assert response.status_code == 404
    env.alice.headers.pop("X-CSRF-Token")
    assert create(env).status_code == 403
    assert env.alice.post(f"{env.base}/experiments/{exp['id']}/abort").status_code == 403


def test_deleting_the_dataset_removes_its_experiments(env: Env, db: Session) -> None:
    create(env)
    assert env.alice.delete(f"{env.base}/datasets/{env.dataset['id']}").status_code == 204
    assert db.scalar(select(func.count()).select_from(Experiment)) == 0


@pytest.mark.parametrize("target", ["dataset", "project"])
def test_deleting_removes_the_whole_validated_and_decided_lifecycle(env: Env, db: Session, target: str) -> None:
    """Decisions are append-only and hypotheses frozen, yet deletion of their dataset or project still cascades."""
    diagnosis = _diagnose(env)
    shipping = next(c["hypotheses"][0] for c in diagnosis["cases"] if c["case_key"] == "shipping")
    exp = create(env, hypothesis_id=shipping["id"]).json()
    run_experiment(env, exp)
    assert env.alice.post(f"{env.base}/experiments/{exp['id']}/validate-hypothesis").status_code == 200
    assert env.alice.post(f"{env.base}/experiments/{exp['id']}/decisions", json={"decision": "approve", "rationale": "fixes it"}).status_code == 201
    path = f"{env.base}/datasets/{env.dataset['id']}" if target == "dataset" else env.base
    assert env.alice.delete(path).status_code == 204
    for model in (Experiment, ExperimentDecision, DiagnosisHypothesis, EvaluationRun):
        assert db.scalar(select(func.count()).select_from(model)) == 0, model.__name__
    assert env.alice.get(f"{env.base}/experiments/{exp['id']}").status_code == 404
