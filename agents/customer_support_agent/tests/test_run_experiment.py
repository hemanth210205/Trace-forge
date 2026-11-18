"""M10-D: both experiment arms must receive identical case inputs and injected failures."""

import inspect

from agents.customer_support_agent import run_experiment
from agents.customer_support_agent.agent import execute_agent_task


def test_task_passes_case_inputs_through_unchanged_for_both_arms() -> None:
    case = {"question": "What items are covered under the standard warranty terms?", "override_mode": "context_selection_failure"}
    snapshot = dict(case)
    for spec in [{"config": run_experiment.BASELINE_CONFIG}, *run_experiment.CANDIDATES.values()]:
        out = run_experiment.experiment_task(case, spec["config"])
        assert case == snapshot  # never mutated
        assert out == execute_agent_task(dict(snapshot), config=spec["config"])  # same as running the raw case


def test_injected_context_selection_failure_still_fails_in_the_candidate_arm() -> None:
    case = {"question": "What items are covered under the standard warranty terms?", "override_mode": "context_selection_failure"}
    expected = "The warranty covers manufacturing defects for 1 year and strictly does not cover accidental damage."
    for spec in run_experiment.CANDIDATES.values():
        assert run_experiment.experiment_task(case, spec["config"])["answer"] != expected


def test_no_arm_specific_input_rewrite_remains() -> None:
    source = inspect.getsource(run_experiment.experiment_task)
    assert "override_mode" not in source and "mode\") ==" not in source
