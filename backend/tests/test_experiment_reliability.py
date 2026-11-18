"""M10-D: experiment reliability. A candidate is never recommended on aggregate pass rate alone."""

from types import SimpleNamespace
from typing import Any

from app.services.experiments import compute_comparison, validation_eligibility


def case(state: str, category: str = "correct", expected_failure: str | None = None, *, controlled: bool = True,
         latency: float = 10.0, tokens: int | None = 100, cost: float | None = 0.0002) -> dict[str, Any]:
    meta = {"category": category, **({"expected_failure": expected_failure} if controlled else {})}
    return {"state": state, "latency_ms": latency, "tokens": tokens, "cost_usd": cost, "evaluations": [], "metadata": meta}


def run(status: str = "completed") -> Any:
    return SimpleNamespace(id="r", status=status)


def compare(base: dict, cand: dict, cand_status: str = "completed") -> dict[str, Any]:
    return compute_comparison(base, cand, run(), run(cand_status))


def codes(out: dict) -> set[str]:
    return {f["code"] for f in out["reliability"]["flags"]}


def test_m9_pattern_injected_failures_passing_is_flagged_not_recommended() -> None:
    # The M9 experiment: two injected context-selection failures "fixed" only because the candidate arm ran them
    # without the injection, plus a regressed multi-intent positive control. Aggregate pass rate went up.
    base = {"c04": case("failed", "context_selection_failure", "context_selection_failure"),
            "c40": case("failed", "context_selection_failure", "context_selection_failure"),
            "c32": case("passed"), "c01": case("passed")}
    cand = {"c04": case("passed", "context_selection_failure", "context_selection_failure"),
            "c40": case("passed", "context_selection_failure", "context_selection_failure"),
            "c32": case("failed"), "c01": case("passed")}
    out = compare(base, cand)
    assert out["pass_rate_delta"] > 0
    rel = out["reliability"]
    assert {"injected_failure_passed", "positive_control_regression", "regression_in_previously_passing"} <= codes(out)
    assert rel["recommendation"] == "not_recommended" and rel["requires_human_approval"] is True
    assert rel["cases"]["genuinely_fixed"] == [] and rel["negative_controls"]["now_passing"] == ["c04", "c40"]
    assert rel["negative_controls"]["baseline_detection_rate"] == 1.0 and rel["negative_controls"]["candidate_detection_rate"] == 0.0
    assert rel["positive_controls"] == {"cases": 2, "baseline_pass_rate": 1.0, "candidate_pass_rate": 0.5, "regressed": ["c32"]}
    assert "Reliability flags" in out["suggested_outcome_reason"] and "human decision" in out["suggested_outcome_reason"]


def test_injected_failure_passing_without_regressions_makes_the_outcome_inconclusive() -> None:
    base = {"n1": case("failed", "grounding_failure", "grounding_failure"), "p1": case("passed")}
    cand = {"n1": case("passed", "grounding_failure", "grounding_failure"), "p1": case("passed")}
    out = compare(base, cand)
    assert out["suggested_outcome"] == "inconclusive" and out["reliability"]["recommendation"] == "not_recommended"


def test_genuine_fix_with_controls_intact_is_only_eligible_for_human_review() -> None:
    base = {"p1": case("failed", "multi_intent"), "p2": case("passed"), "n1": case("failed", "tool_execution_failure", "tool_execution_failure")}
    cand = {"p1": case("passed", "multi_intent"), "p2": case("passed"), "n1": case("failed", "tool_execution_failure", "tool_execution_failure")}
    out = compare(base, cand)
    rel = out["reliability"]
    assert out["suggested_outcome"] == "candidate_improves"
    assert rel["recommendation"] == "eligible_for_human_review"  # never "promote"
    assert rel["cases"]["genuinely_fixed"] == ["p1"] and not [f for f in rel["flags"] if f["blocking"]]
    assert rel["negative_controls"]["candidate_detection_rate"] == 1.0


def test_category_tradeoff_is_flagged() -> None:
    base = {"a1": case("failed", "shipping"), "a2": case("failed", "shipping"),
            "b1": case("passed", "warranty"), "b2": case("passed", "warranty"), "b3": case("passed", "warranty")}
    cand = {"a1": case("passed", "shipping"), "a2": case("passed", "shipping"),
            "b1": case("failed", "warranty"), "b2": case("passed", "warranty"), "b3": case("passed", "warranty")}
    out = compare(base, cand)
    assert out["pass_rate_delta"] > 0 and "category_tradeoff" in codes(out)
    per = out["reliability"]["per_category"]
    assert per["shipping"]["delta"] == 1.0 and per["warranty"]["delta"] == -0.3333
    assert out["reliability"]["recommendation"] == "not_recommended"


def test_injected_failure_already_passing_in_baseline_is_reported_but_not_blocking() -> None:
    base = {"n1": case("passed", "reasoning_failure", "reasoning_failure"), "p1": case("failed")}
    cand = {"n1": case("passed", "reasoning_failure", "reasoning_failure"), "p1": case("passed")}
    out = compare(base, cand)
    flag = next(f for f in out["reliability"]["flags"] if f["code"] == "injected_failure_not_reproduced")
    assert flag["blocking"] is False and flag["cases"] == ["n1"]
    assert out["reliability"]["recommendation"] == "eligible_for_human_review"


def test_no_change_is_not_an_improvement() -> None:
    base = {"p1": case("passed"), "p2": case("failed")}
    out = compare(base, dict(base))
    assert out["reliability"]["recommendation"] == "no_improvement"
    assert out["reliability"]["cases"]["unchanged_pass"] == ["p1"] and out["reliability"]["cases"]["unchanged_fail"] == ["p2"]


def test_without_control_metadata_controls_are_reported_unavailable() -> None:
    base = {"a": case("failed", controlled=False), "b": case("passed", controlled=False)}
    cand = {"a": case("passed", controlled=False), "b": case("passed", controlled=False)}
    out = compare(base, cand)
    assert "controls_unavailable" in codes(out)
    assert out["reliability"]["positive_controls"]["cases"] == 0 and out["reliability"]["negative_controls"]["baseline_detection_rate"] is None


def test_latency_token_and_cost_differences_are_reported_on_paired_cases() -> None:
    base = {"a": case("passed", latency=10, tokens=100, cost=0.001), "b": case("passed", tokens=None, cost=None)}
    cand = {"a": case("passed", latency=14, tokens=130, cost=0.0013), "b": case("passed", tokens=50, cost=None)}
    rel = compare(base, cand)["reliability"]
    assert rel["latency_ms"]["delta"] == 2.0 and rel["latency_ms"]["paired_cases"] == 2  # mean of (+4, 0)
    assert rel["tokens"] == {"available": True, "paired_cases": 1, "baseline_mean": 100, "candidate_mean": 130, "delta": 30}
    assert rel["cost_usd"]["paired_cases"] == 1 and rel["cost_usd"]["delta"] == 0.0003


def test_incomplete_arm_is_inconclusive() -> None:
    out = compare({"a": case("passed")}, {"a": case("missing")}, cand_status="incomplete")
    assert out["reliability"]["recommendation"] == "inconclusive" and "incomplete_arms" in codes(out)


def test_hypothesis_on_an_injected_failure_that_passed_only_in_the_candidate_is_not_validated() -> None:
    base = {"c04": case("failed", "context_selection_failure", "context_selection_failure")}
    cand = {"c04": case("passed", "context_selection_failure", "context_selection_failure")}
    experiment = SimpleNamespace(status="completed", comparison=compare(base, cand))
    eligible, reason = validation_eligibility(experiment, "c04")
    assert eligible is False and "injected failure" in reason
    # Historical comparisons without a reliability block keep their original behaviour.
    legacy = SimpleNamespace(status="completed", comparison={"cases": {"fixed": ["c04"]}})
    assert validation_eligibility(legacy, "c04")[0] is True
