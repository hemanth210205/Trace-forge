"""The diagnosis workflow: an explicit, typed state machine.

LangGraph is not a dependency (every current release pulls in ``httpx2``, which would switch Starlette's test
client away from the pinned ``httpx``). The workflow keeps the same shape a LangGraph graph would have: a typed
state object, one function per node, a declared transition table that is validated at every hop, and hard
bounds on steps, time and provider retries.

    load_run → collect_failed_cases → gather_traces → analyze_execution → compare_outputs →
    propose_hypotheses → assess_evidence → score_confidence → identify_missing_evidence →
    recommend_experiments → persist → done            (any step may end in → failed)
"""

import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import scrub
from app.db.base import utcnow
from app.diagnosis import evidence as ev
from app.diagnosis.assess import assess
from app.diagnosis.providers import DiagnosisProvider, ProviderError
from app.diagnosis.schemas import ANSWER_EVALUATORS, CaseDiagnosisResult, Fact, WorkflowState
from app.models import Diagnosis, DiagnosisCase, DiagnosisHypothesis

logger = logging.getLogger("traceforge.diagnosis")

STEPS = (
    "load_run", "collect_failed_cases", "gather_traces", "analyze_execution", "compare_outputs",
    "propose_hypotheses", "assess_evidence", "score_confidence", "identify_missing_evidence",
    "recommend_experiments", "persist",
)
TRANSITIONS: dict[str, str] = {**{a: b for a, b in zip(STEPS, STEPS[1:])}, STEPS[-1]: "done"}
STATUS_ORDER = {"supported": 0, "unsupported": 1, "inconclusive": 2, "contradicted": 3}


class WorkflowError(Exception):
    """Ends the workflow in ``failed`` with a message safe to show to the user."""


class DiagnosisWorkflow:
    def __init__(
        self,
        provider: DiagnosisProvider,
        read_factory: sessionmaker[Session],
        write_factory: sessionmaker[Session],
        *,
        max_cases: int,
        time_limit_seconds: float,
        max_retries: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.provider = provider
        self.read_factory = read_factory
        self.write_factory = write_factory
        self.max_cases = max_cases
        self.time_limit = time_limit_seconds
        self.max_retries = max_retries
        self.clock = clock
        self.sleep = sleep
        self._raw: dict[str, tuple[dict[str, Any], bool, list[dict[str, Any]], bool]] = {}

    # --- engine ------------------------------------------------------------------------------------------------

    def run(self, state: WorkflowState) -> WorkflowState:
        started = self.started = self.clock()
        self.deadline = started + self.time_limit
        step = STEPS[0]
        for _ in range(len(STEPS) + 1):  # hard step bound: the table is acyclic, so this is never reached
            self._check_deadline()
            state.stats.steps.append(step)  # recorded on entry, so the persisted stats include "persist"
            getattr(self, step)(state)
            nxt = TRANSITIONS.get(step)
            if nxt is None or (nxt != "done" and nxt not in STEPS):
                raise WorkflowError(f"Invalid workflow transition from {step!r}.")
            if nxt == "done":
                state.stats.duration_ms = round((self.clock() - started) * 1000, 1)
                return state
            step = nxt
        raise WorkflowError("Workflow exceeded its step limit.")

    def _check_deadline(self) -> None:
        if self.clock() > self.deadline:
            raise WorkflowError(f"Diagnosis exceeded its time limit of {self.time_limit:.0f}s.")

    # --- nodes -------------------------------------------------------------------------------------------------

    def load_run(self, state: WorkflowState) -> None:
        with ev.read_only_session(self.read_factory) as rs:
            run = ev.load_run(rs, uuid.UUID(state.project_id), uuid.UUID(state.run_id))
        if run is None:
            raise WorkflowError("The evaluation run no longer exists.")
        if run["status"] not in ("completed", "incomplete"):
            raise WorkflowError(f"Only scored runs can be diagnosed; this run is {run['status']}.")
        state.run = run

    def collect_failed_cases(self, state: WorkflowState) -> None:
        with ev.read_only_session(self.read_factory) as rs:
            failed = ev.load_failed_results(rs, uuid.UUID(state.run_id))
        if not failed:
            raise WorkflowError("The run has no failed cases to diagnose.")
        state.stats.failed_cases = len(failed)
        state.failed_results = failed[: self.max_cases]
        state.stats.skipped_cases = max(0, len(failed) - self.max_cases)

    def gather_traces(self, state: WorkflowState) -> None:
        with ev.read_only_session(self.read_factory) as rs:
            for case in state.failed_results:
                found, spans, truncated = (False, [], False)
                if case["trace_id"]:
                    found, spans, truncated = ev.load_trace(rs, uuid.UUID(state.project_id), case["trace_id"])
                self._raw[case["result_id"]] = (case, found, spans, truncated)

    def analyze_execution(self, state: WorkflowState) -> None:
        state.evidence = [ev.build_case_evidence(case, found, spans, truncated) for case, found, spans, truncated in self._raw.values()]

    def compare_outputs(self, state: WorkflowState) -> None:
        """Adds expected-vs-actual comparison facts for the answer evaluators that failed."""
        for item in state.evidence:
            case = self._raw[item.result_id][0]
            for outcome in case["evaluations"]:
                if outcome.get("type") not in ANSWER_EVALUATORS or outcome.get("passed") or not outcome.get("applicable"):
                    continue
                expected, actual = outcome.get("expected"), outcome.get("actual")
                if expected is None or actual is None:
                    continue
                share = ev.overlap(ev.content_words(expected), ev.content_words(actual))
                fact = Fact(id=f"F{len(item.facts) + 1}", kind="comparison",
                            statement=f"Only {share:.0%} of the expected output's content words appear in the actual output "
                                      f"({outcome['evaluator']}).", trace_id=item.trace_id)
                item.facts.append(fact)
                item.signals.fact_refs.setdefault("answer_failed", []).append(fact.id)

    def propose_hypotheses(self, state: WorkflowState) -> None:
        for item in state.evidence:
            state.proposals[item.result_id] = self._call_provider(state, item)

    def _call_provider(self, state: WorkflowState, item: Any) -> Any:
        attempt = 0
        while True:
            self._check_deadline()
            state.stats.provider_calls += 1
            try:
                return self.provider.propose(item)
            except ProviderError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise WorkflowError(f"Provider {self.provider.name} failed on case {item.case_key}: {exc}") from None
                attempt += 1
                state.stats.provider_retries += 1
                self.sleep(min(0.5 * 2 ** (attempt - 1), max(0.0, self.deadline - self.clock())))

    def assess_evidence(self, state: WorkflowState) -> None:
        for item in state.evidence:
            proposal = state.proposals[item.result_id]
            assessed, case_discarded = [], 0
            for h in proposal.hypotheses:
                result, discarded = assess(h, item)
                case_discarded += discarded
                assessed.append(result)
            state.stats.discarded_references += case_discarded
            state.results.append(CaseDiagnosisResult(evidence=item, summary=proposal.summary.strip(), hypotheses=assessed,
                                                     discarded_references=case_discarded))

    def score_confidence(self, state: WorkflowState) -> None:
        for result in state.results:
            # One hypothesis per category (the strongest), ordered by evidence status, then confidence.
            best: dict[str, Any] = {}
            for h in result.hypotheses:
                key = (STATUS_ORDER[h.evidence_status], -(h.confidence or 0))
                if h.category not in best or key < (STATUS_ORDER[best[h.category].evidence_status], -(best[h.category].confidence or 0)):
                    best[h.category] = h
            result.hypotheses = sorted(best.values(), key=lambda h: (STATUS_ORDER[h.evidence_status], -(h.confidence or 0)))

    def identify_missing_evidence(self, state: WorkflowState) -> None:
        for result in state.results:
            if not result.hypotheses:
                result.evidence.missing_evidence.append("The provider proposed no hypothesis for this case.")
            for h in result.hypotheses:
                if h.evidence_status in ("unsupported", "inconclusive") and not h.missing_evidence:
                    h.missing_evidence = ["Evidence that distinguishes this cause from the alternatives (see the recommended experiment)."]

    def recommend_experiments(self, state: WorkflowState) -> None:
        supported = [h.category for r in state.results for h in r.hypotheses if h.evidence_status == "supported"]
        inconclusive = sum(1 for r in state.results if all(h.evidence_status == "inconclusive" for h in r.hypotheses))
        parts = [f"Analyzed {len(state.results)} failed case(s)"]
        if state.stats.skipped_cases:
            parts.append(f"{state.stats.skipped_cases} more were not analyzed (case limit)")
        if supported:
            counts: dict[str, int] = {}
            for c in supported:
                counts[c] = counts.get(c, 0) + 1
            parts.append("evidence supports " + ", ".join(f"{c.replace('_', ' ')} ({n})" for c, n in counts.items()))
        if inconclusive:
            parts.append(f"{inconclusive} case(s) lack the evidence for any conclusion")
        state.summary = "; ".join(parts) + ". No cause is experimentally validated yet."

    def persist(self, state: WorkflowState) -> None:
        with self.write_factory() as db:
            diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.id == uuid.UUID(state.diagnosis_id)).with_for_update())
            if diagnosis is None or diagnosis.status != "running":
                raise WorkflowError("The diagnosis is no longer running.")
            for position, result in enumerate(state.results, start=1):
                ev_ = result.evidence
                case = DiagnosisCase(
                    diagnosis_id=diagnosis.id, result_id=uuid.UUID(ev_.result_id), position=position, case_key=ev_.case_key,
                    trace_id=uuid.UUID(ev_.trace_id) if ev_.trace_id else None,
                    facts=[f.model_dump() for f in ev_.facts], missing_evidence=ev_.missing_evidence, summary=result.summary,
                )
                db.add(case)
                db.flush()
                for rank, h in enumerate(result.hypotheses, start=1):
                    db.add(DiagnosisHypothesis(diagnosis_id=diagnosis.id, case_id=case.id, rank=rank, **h.model_dump()))
            # Write every child row while the diagnosis is still "running": the unit of work would otherwise
            # update the parent first, and the database rejects rows added to a completed diagnosis.
            db.flush()
            state.stats.duration_ms = round((self.clock() - self.started) * 1000, 1)
            stats = state.stats.model_dump()
            diagnosis.summary = state.summary
            diagnosis.stats = stats
            diagnosis.finished_at = utcnow()
            diagnosis.status = "completed"
            db.commit()


def execute_diagnosis(
    diagnosis_id: uuid.UUID,
    factory: sessionmaker[Session],
    provider_factory: Callable[[], DiagnosisProvider],
    *,
    max_cases: int,
    time_limit_seconds: float,
    max_retries: int,
) -> None:
    """Runs one pending diagnosis to completion or failure. Never raises (it runs as a background task)."""
    with factory() as db:
        diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.id == diagnosis_id).with_for_update())
        if diagnosis is None or diagnosis.status != "pending":
            return
        diagnosis.status = "running"
        diagnosis.started_at = utcnow()
        state = WorkflowState(diagnosis_id=str(diagnosis.id), project_id=str(diagnosis.project_id), run_id=str(diagnosis.run_id))
        db.commit()
    try:
        provider = provider_factory()
        workflow = DiagnosisWorkflow(provider, factory, factory, max_cases=max_cases,
                                     time_limit_seconds=time_limit_seconds, max_retries=max_retries)
        workflow.run(state)
    except Exception as exc:
        message = str(exc) if isinstance(exc, (WorkflowError, ProviderError)) else f"Internal error ({type(exc).__name__})."
        logger.warning("diagnosis %s failed: %s", diagnosis_id, type(exc).__name__, exc_info=not isinstance(exc, (WorkflowError, ProviderError)))
        with factory() as db:
            diagnosis = db.scalar(select(Diagnosis).where(Diagnosis.id == diagnosis_id).with_for_update())
            if diagnosis is not None and diagnosis.status in ("pending", "running"):
                diagnosis.stats = state.stats.model_dump()
                diagnosis.error = scrub(message)[:1000]
                diagnosis.finished_at = utcnow()
                diagnosis.status = "failed"
                db.commit()
