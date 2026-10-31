"""Diagnosis workflow through the API on real PostgreSQL: persistence, references, failures, isolation."""

import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.diagnosis import evidence as ev
from app.diagnosis.providers import (
    AnthropicProvider,
    MockProvider,
    ProviderConfigurationError,
    ProviderError,
    ProviderMalformedOutput,
    ProviderTransientError,
)
from app.diagnosis.schemas import ProviderOutput, WorkflowState
from app.diagnosis.workflow import DiagnosisWorkflow, WorkflowError
from app.models import Diagnosis, DiagnosisHypothesis, Span, Trace
from tests.conftest import ApiClient
from tests.test_evaluation_runs import Env

EVALUATORS = [{"type": "normalized_match", "field": "answer"}, {"type": "source_match"}]


@pytest.fixture
def provider_slot(app: FastAPI) -> Iterator[dict[str, Any]]:
    """Swap the provider factory on app.state by hand (pytest's monkeypatch cannot restore Starlette State)."""
    slot: dict[str, Any] = {"factory": lambda _settings: MockProvider()}
    app.state.diagnosis_provider_factory = lambda settings: slot["factory"](settings)
    try:
        yield slot
    finally:
        del app.state.diagnosis_provider_factory


@pytest.fixture
def scored(alice: ApiClient, client: ApiClient, provider_slot) -> tuple[Env, dict[str, Any]]:
    """A completed run: shipping passes, refunds retrieves the wrong document, warranty has no trace."""
    env = Env(alice, client)
    run = env.create_run(EVALUATORS)
    ids = env.executor_cases(run)
    env.submit(run, [
        {"case_id": ids["shipping"], "output": {"answer": "Your order is shipped."}, "trace_id": env.trace(["kb/shipping.md"])},
        {"case_id": ids["refunds"], "output": {"answer": "Refunds take 5 days."}, "trace_id": env.trace(["kb/warranty.md"], error=True)},
        {"case_id": ids["warranty"], "output": {"answer": "Two years."}},
    ])
    finished = env.finish(run).json()
    assert finished["status"] == "completed" and finished["summary"]["failed"] == 2
    return env, finished


def start(env: Env, run: dict[str, Any]) -> Any:
    return env.alice.post(f"{env.base}/eval-runs/{run['id']}/diagnoses")


def test_diagnosis_persists_evidence_backed_hypotheses_with_resolvable_references(scored, db: Session) -> None:
    env, run = scored
    response = start(env, run)
    assert response.status_code == 202 and response.json()["status"] == "pending"
    detail = env.alice.get(f"{env.base}/diagnoses/{response.json()['id']}").json()
    assert detail["status"] == "completed" and detail["provider"] == "mock" and detail["error"] is None
    assert detail["stats"]["steps"][0] == "load_run" and detail["stats"]["steps"][-1] == "persist" and len(detail["stats"]["steps"]) == 11
    assert "No cause is experimentally validated yet" in detail["summary"]
    cases = {c["case_key"]: c for c in detail["cases"]}
    assert set(cases) == {"refunds", "warranty"}

    refunds = cases["refunds"]
    by_category = {h["category"]: h for h in refunds["hypotheses"]}
    retrieval = by_category["retrieval_failure"]
    assert retrieval["evidence_status"] == "supported" and retrieval["validation_status"] == "unvalidated"
    assert "kb/returns.md" in retrieval["explanation"]
    # The tool failed but the answer evaluator passed: the evidence contradicts a tool-caused wrong answer.
    assert by_category["tool_execution_failure"]["evidence_status"] == "contradicted"
    assert "recovered" in by_category["tool_execution_failure"]["uncertainty"]
    assert [h["evidence_status"] for h in refunds["hypotheses"]] == sorted(
        [h["evidence_status"] for h in refunds["hypotheses"]], key=["supported", "unsupported", "inconclusive", "contradicted"].index)

    # Every trace and span reference is a real row of this project.
    trace_ids = {t for c in detail["cases"] for h in c["hypotheses"] for t in h["evidence_trace_ids"]}
    span_ids = {s for c in detail["cases"] for h in c["hypotheses"] for s in h["evidence_span_ids"]}
    span_ids |= {f["span_id"] for c in detail["cases"] for f in c["facts"] if f.get("span_id")}
    pid = uuid.UUID(env.project["id"])
    assert trace_ids and db.scalar(select(func.count()).select_from(Trace).where(Trace.project_id == pid, Trace.id.in_(map(uuid.UUID, trace_ids)))) == len(trace_ids)
    assert span_ids and db.scalar(select(func.count()).select_from(Span).where(Span.project_id == pid, Span.id.in_(map(uuid.UUID, span_ids)))) == len(span_ids)
    fact_ids = {f["id"] for f in refunds["facts"]}
    assert all(set(h["supporting_facts"]) | set(h["contradicting_facts"]) <= fact_ids for h in refunds["hypotheses"])

    warranty = cases["warranty"]
    assert warranty["trace_id"] is None
    assert [h["category"] for h in warranty["hypotheses"]] == ["insufficient_evidence"]
    assert warranty["hypotheses"][0]["confidence"] is None and warranty["hypotheses"][0]["evidence_span_ids"] == []

    hyp = env.alice.get(f"{env.base}/diagnoses/{detail['id']}/hypotheses/{retrieval['id']}").json()
    assert {f["id"] for f in hyp["facts"]} == set(retrieval["supporting_facts"]) | set(retrieval["contradicting_facts"])
    listing = env.alice.get(f"{env.base}/diagnoses", params={"run_id": run["id"]}).json()
    assert [d["id"] for d in listing] == [detail["id"]] and listing[0]["category_counts"]["retrieval_failure"] == 1


def test_rerunning_keeps_history_and_is_deterministic(scored) -> None:
    env, run = scored
    first = env.alice.get(f"{env.base}/diagnoses/{start(env, run).json()['id']}").json()
    retry = env.alice.post(f"{env.base}/diagnoses/{first['id']}/retry")
    assert retry.status_code == 202 and retry.json()["retry_of"] == first["id"]
    second = env.alice.get(f"{env.base}/diagnoses/{retry.json()['id']}").json()
    assert env.alice.get(f"{env.base}/diagnoses/{first['id']}").json() == first  # unchanged

    def shape(d: dict) -> list:
        return [(c["case_key"], [(h["category"], h["evidence_status"], h["confidence"], h["supporting_facts"]) for h in c["hypotheses"]])
                for c in d["cases"]]

    assert shape(first) == shape(second)
    assert len(env.alice.get(f"{env.base}/diagnoses").json()) == 2


def test_only_scored_runs_with_failures_can_be_diagnosed(alice: ApiClient, client: ApiClient, provider_slot) -> None:
    env = Env(alice, client)
    pending = env.create_run(EVALUATORS)
    assert start(env, pending).json()["error"]["code"] == "run_not_scored"
    clean = env.create_run([{"type": "required_fields", "fields": ["answer"]}])
    ids = env.executor_cases(clean)
    env.submit(clean, [{"case_id": i, "output": {"answer": "ok"}} for i in ids.values()])
    env.finish(clean)
    response = start(env, clean)
    assert response.status_code == 409 and response.json()["error"]["code"] == "nothing_to_diagnose"


class Scripted:
    """Provider that raises the scripted errors in order, then answers like the mock provider."""

    name, model = "scripted", "fake-1"

    def __init__(self, errors: list[Exception]) -> None:
        self.errors, self.calls = list(errors), 0

    def propose(self, evidence: Any) -> ProviderOutput:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return MockProvider().propose(evidence)


def test_transient_provider_errors_are_retried_within_bounds(scored, provider_slot) -> None:
    env, run = scored
    scripted = Scripted([ProviderTransientError("timeout"), ProviderMalformedOutput("bad json")])
    provider_slot["factory"] = lambda _s: scripted
    detail = env.alice.get(f"{env.base}/diagnoses/{start(env, run).json()['id']}").json()
    assert detail["status"] == "completed" and detail["provider"] == "scripted" and detail["model"] == "fake-1"
    assert detail["stats"]["provider_retries"] == 2 and detail["stats"]["provider_calls"] == 4  # 2 cases + 2 retries


@pytest.mark.parametrize(
    ("errors", "calls"),
    [([ProviderMalformedOutput("not json")] * 5, 3), ([ProviderError("model declined")], 1)],
    ids=["malformed-until-retries-run-out", "non-retryable"],
)
def test_provider_failure_fails_the_diagnosis_and_persists_nothing(scored, provider_slot, db: Session, errors, calls) -> None:
    env, run = scored
    scripted = Scripted(errors)
    provider_slot["factory"] = lambda _s: scripted
    detail = env.alice.get(f"{env.base}/diagnoses/{start(env, run).json()['id']}").json()
    assert detail["status"] == "failed" and detail["cases"] == [] and detail["finished_at"]
    assert "Provider scripted failed on case" in detail["error"]
    assert scripted.calls == calls
    assert db.scalar(select(func.count()).select_from(DiagnosisHypothesis)) == 0
    assert env.alice.post(f"{env.base}/diagnoses/{detail['id']}/retry").status_code == 202  # retry is allowed


def test_provider_configuration_error_is_a_503_without_secrets(scored, provider_slot, db: Session) -> None:
    env, run = scored

    def broken(_settings: Settings) -> Any:
        raise ProviderConfigurationError("ANTHROPIC_API_KEY is not configured on the server.")

    provider_slot["factory"] = broken
    response = start(env, run)
    assert response.status_code == 503 and response.json()["error"]["code"] == "diagnosis_provider_unavailable"
    assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0


def test_time_limit_ends_the_workflow(scored, app: FastAPI, provider_slot) -> None:
    env, run = scored
    ticks = iter([0.0] + [1000.0] * 50)
    workflow = DiagnosisWorkflow(MockProvider(), app.state.sessionmaker, app.state.sessionmaker, max_cases=10,
                                 time_limit_seconds=5, max_retries=0, clock=lambda: next(ticks))
    with pytest.raises(WorkflowError, match="time limit"):
        workflow.run(WorkflowState(diagnosis_id=str(uuid.uuid4()), project_id=env.project["id"], run_id=run["id"]))


def test_evidence_reads_run_in_a_read_only_transaction(app: FastAPI) -> None:
    with ev.read_only_session(app.state.sessionmaker) as rs:
        with pytest.raises(DBAPIError, match="read-only transaction"):
            rs.execute(text("INSERT INTO users (id, email, password_hash, display_name) VALUES (gen_random_uuid(), 'x@y.z', 'h', 'n')"))


def test_finished_diagnoses_are_immutable_in_the_database(scored, db: Session) -> None:
    env, run = scored
    did = start(env, run).json()["id"]
    hypothesis_id = db.scalar(select(DiagnosisHypothesis.id).where(DiagnosisHypothesis.diagnosis_id == uuid.UUID(did)).limit(1))
    for statement in (
        "UPDATE diagnoses SET summary = 'tampered' WHERE id = :d",
        "UPDATE diagnosis_hypotheses SET evidence_status = 'supported', confidence = 1 WHERE diagnosis_id = :d",
        "DELETE FROM diagnosis_cases WHERE diagnosis_id = :d",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text(statement), {"d": did})
        db.rollback()
    with pytest.raises(DBAPIError, match="validated_needs_reference"):
        db.execute(text("UPDATE diagnosis_hypotheses SET validation_status = 'validated' WHERE id = :h"), {"h": hypothesis_id})
    db.rollback()
    # Reserved for M7: an experiment may record validation (and nothing else) on a finished diagnosis.
    db.execute(text("UPDATE diagnosis_hypotheses SET validation_status = 'validated', validated_by = 'experiment:test' WHERE id = :h"),
               {"h": hypothesis_id})
    db.rollback()


def test_diagnoses_are_isolated_between_users_and_projects(scored, bob: ApiClient) -> None:
    env, run = scored
    did = start(env, run).json()["id"]
    hid = env.alice.get(f"{env.base}/diagnoses/{did}").json()["cases"][0]["hypotheses"][0]["id"]
    other = env.alice.create_project("Other", slug="other")
    other_base = f"/api/v1/projects/{other['id']}"
    for response in (
        bob.post(f"{env.base}/eval-runs/{run['id']}/diagnoses"), bob.get(f"{env.base}/diagnoses"),
        bob.get(f"{env.base}/diagnoses/{did}"), bob.post(f"{env.base}/diagnoses/{did}/retry"),
        bob.get(f"{env.base}/diagnoses/{did}/hypotheses/{hid}"),
        env.alice.get(f"{other_base}/diagnoses/{did}"), env.alice.post(f"{other_base}/eval-runs/{run['id']}/diagnoses"),
    ):
        assert response.status_code == 404
    assert env.alice.get(f"{other_base}/diagnoses").json() == []


def test_diagnosis_writes_require_csrf(scored) -> None:
    env, run = scored
    env.alice.headers.pop("X-CSRF-Token")
    assert start(env, run).status_code == 403


def test_deleting_the_project_deletes_its_diagnoses(scored, db: Session) -> None:
    env, run = scored
    start(env, run)
    assert env.alice.delete(env.base).status_code == 204
    assert db.scalar(select(func.count()).select_from(Diagnosis)) == 0


# --- Anthropic provider wrapper (fake SDK client: no network, no key) --------------------------------------------


class FakeMessages:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour, self.calls = behaviour, []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def anthropic_provider(settings: Settings, behaviour: Any) -> tuple[AnthropicProvider, FakeMessages]:
    messages = FakeMessages(behaviour)
    return AnthropicProvider(settings, client=SimpleNamespace(messages=messages)), messages


def evidence_for_provider() -> Any:
    from tests.test_diagnosis_reasoning import case, outcome, retrieval  # shared builders

    return ev.build_case_evidence(case([outcome("normalized_match", False, "a", "b")], sources=["kb/returns.md"]),
                                  True, [retrieval(["kb/returns.md"])], False)


def test_anthropic_provider_requests_structured_output_and_returns_it(settings: Settings) -> None:
    good = ProviderOutput(summary="s", hypotheses=[])
    provider, messages = anthropic_provider(settings, SimpleNamespace(stop_reason="end_turn", parsed_output=good))
    assert provider.propose(evidence_for_provider()) is good
    call = messages.calls[0]
    assert call["model"] == settings.diagnosis_model and call["output_format"] is ProviderOutput
    assert "<facts>" in call["messages"][0]["content"] and "untrusted" in call["system"]


class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    status_code = 429


class AuthenticationError(Exception):
    status_code = 401


class BadRequestError(Exception):
    status_code = 400

    def __init__(self) -> None:
        super().__init__("bad")
        self.message = "invalid x-api-key sk-ant-api03-SECRETSECRETSECRETSECRET"


@pytest.mark.parametrize(
    ("behaviour", "expected"),
    [
        (SimpleNamespace(stop_reason="end_turn", parsed_output=None), ProviderMalformedOutput),
        (SimpleNamespace(stop_reason="max_tokens", parsed_output=None), ProviderMalformedOutput),
        (SimpleNamespace(stop_reason="refusal", parsed_output=None), ProviderError),
        (APITimeoutError("read timed out"), ProviderTransientError),
        (RateLimitError("slow down"), ProviderTransientError),
        (AuthenticationError("no"), ProviderConfigurationError),
        (BadRequestError(), ProviderError),
    ],
    ids=["no-output", "truncated", "refusal", "timeout", "rate-limit", "auth", "bad-request"],
)
def test_anthropic_provider_maps_failures(settings: Settings, behaviour: Any, expected: type) -> None:
    provider, _ = anthropic_provider(settings, behaviour)
    with pytest.raises(expected) as exc:
        provider.propose(evidence_for_provider())
    assert type(exc.value) is expected
    assert "SECRET" not in str(exc.value)


def test_anthropic_provider_maps_invalid_structured_output(settings: Settings) -> None:
    try:
        ProviderOutput.model_validate({"summary": "s", "hypotheses": [{"category": "made_up"}]})
    except ValidationError as error:
        provider, _ = anthropic_provider(settings, error)
    with pytest.raises(ProviderMalformedOutput):
        provider.propose(evidence_for_provider())


def test_anthropic_provider_requires_server_side_configuration(settings: Settings) -> None:
    with pytest.raises(ProviderConfigurationError, match="ANTHROPIC_API_KEY is not configured"):
        AnthropicProvider(settings.model_copy(update={"anthropic_api_key": None}))
    with pytest.raises(ProviderConfigurationError, match="not installed|credentials") as exc:
        from pydantic import SecretStr

        AnthropicProvider(settings.model_copy(update={"anthropic_api_key": SecretStr("sk-ant-api03-" + "k" * 30)}))
    assert "sk-ant" not in str(exc.value)
