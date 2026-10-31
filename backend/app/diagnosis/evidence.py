"""Read-only evidence access and deterministic fact extraction.

The functions below are the *only* way the diagnosis reaches the database. They run inside a transaction
marked ``READ ONLY`` (PostgreSQL rejects any write in it), always filter by the run's project, and bound how
much they load. Everything read from traces (inputs, documents, tool and model outputs) is untrusted data: it
is quoted into fact statements and never interpreted as instructions.
"""

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.diagnosis.schemas import ANSWER_EVALUATORS, FORMAT_EVALUATORS, CaseEvidence, Fact, Signals
from app.models import DatasetVersionCase, EvaluationResult, EvaluationRun, Span, Trace

MAX_SPANS_PER_TRACE = 200
MAX_FACTS_PER_CASE = 60
PREVIEW_CHARS = 160
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have i if in is it its my of on or our that the their "
    "this to was we what when where which will with you your".split()
)


@contextmanager
def read_only_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        try:
            yield session
        finally:
            session.rollback()


def load_run(rs: Session, project_id: uuid.UUID, run_id: uuid.UUID) -> dict[str, Any] | None:
    run = rs.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id, EvaluationRun.project_id == project_id))
    if run is None:
        return None
    return {"id": str(run.id), "name": run.name, "status": run.status, "summary": run.summary,
            "evaluators": run.evaluators, "dataset_version_id": str(run.dataset_version_id)}


def load_failed_results(rs: Session, run_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = rs.execute(
        select(EvaluationResult, DatasetVersionCase)
        .join(DatasetVersionCase, DatasetVersionCase.id == EvaluationResult.case_id)
        .where(EvaluationResult.run_id == run_id, EvaluationResult.passed.is_(False))
        .order_by(DatasetVersionCase.position)
    ).all()
    return [
        {
            "result_id": str(r.id), "case_key": c.case_key, "input": c.input, "expected_output": c.expected_output,
            "expected_sources": list(c.expected_sources or []), "output": r.output, "execution_error": r.execution_error,
            "trace_id": str(r.trace_id) if r.trace_id else None, "evaluations": list(r.evaluations or []),
        }
        for r, c in rows
    ]


def load_trace(rs: Session, project_id: uuid.UUID, trace_id: str) -> tuple[bool, list[dict[str, Any]], bool]:
    """(exists in project, spans in start order, truncated)."""
    try:
        tid = uuid.UUID(trace_id)
    except ValueError:
        return False, [], False
    if rs.scalar(select(Trace.id).where(Trace.project_id == project_id, Trace.id == tid)) is None:
        return False, [], False
    spans = rs.scalars(
        select(Span).where(Span.project_id == project_id, Span.trace_id == tid)
        .order_by(Span.started_at, Span.id).limit(MAX_SPANS_PER_TRACE + 1)
    ).all()
    rows = [
        {"id": str(s.id), "name": s.name, "kind": s.kind, "status": s.status, "input": s.input, "output": s.output,
         "attributes": s.attributes or {}, "error": s.error, "model": s.model}
        for s in spans[:MAX_SPANS_PER_TRACE]
    ]
    return True, rows, len(spans) > MAX_SPANS_PER_TRACE


# --- fact extraction -------------------------------------------------------------------------------------------


def _preview(value: Any, limit: int = PREVIEW_CHARS) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    raw = " ".join(raw.split())
    return json.dumps(raw[:limit] + ("…" if len(raw) > limit else ""), ensure_ascii=False)  # quoted: data, not prose


def content_words(value: Any) -> set[str]:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return {w for w in re.findall(r"[a-z0-9]+", raw.lower()) if w not in STOPWORDS}


def overlap(answer: set[str], other: set[str]) -> float:
    """Share of the answer's content words that also occur in ``other`` (lexical containment)."""
    return round(len(answer & other) / len(answer), 3) if answer else 0.0


def _answer_value(case: dict[str, Any]) -> Any:
    for outcome in case["evaluations"]:
        if outcome.get("type") in ANSWER_EVALUATORS and outcome.get("applicable") and outcome.get("actual") is not None:
            return outcome["actual"]
    return case["output"]


class _Facts:
    def __init__(self) -> None:
        self.items: list[Fact] = []

    def add(self, kind: str, statement: str, trace_id: str | None = None, span_id: str | None = None) -> str:
        fact_id = f"F{len(self.items) + 1}"
        if len(self.items) < MAX_FACTS_PER_CASE:
            self.items.append(Fact(id=fact_id, kind=kind, statement=statement, trace_id=trace_id, span_id=span_id))  # type: ignore[arg-type]
        return fact_id


def build_case_evidence(case: dict[str, Any], trace_found: bool, spans: list[dict[str, Any]], truncated: bool) -> CaseEvidence:
    facts, s, missing = _Facts(), Signals(), []
    refs: dict[str, list[str]] = {}

    def ref(signal: str, fact_id: str) -> None:
        refs.setdefault(signal, []).append(fact_id)

    # 1. Evaluation outcomes and execution errors.
    for o in case["evaluations"]:
        if not o.get("applicable"):
            continue
        verdict = "passed" if o.get("passed") else "failed"
        statement = f"Evaluator {o['evaluator']} ({o['type']}) {verdict}: {o.get('explanation', '')}"
        if not o.get("passed") and o["type"] in ANSWER_EVALUATORS | {"source_match"}:
            statement += f" Expected {_preview(o.get('expected'))}; actual {_preview(o.get('actual'))}."
        fid = facts.add("evaluation", statement)
        if o["type"] in ANSWER_EVALUATORS:
            ref("answer_failed" if not o.get("passed") else "answer_passed", fid)
            s.answer_failed |= not o.get("passed")
            s.answer_passed_any |= bool(o.get("passed"))
        elif o["type"] in FORMAT_EVALUATORS or (o["type"] == "json_match" and "not valid JSON" in o.get("explanation", "")):
            if not o.get("passed"):
                s.format_failed = True
                ref("format_failed", fid)
            else:
                ref("format_passed", fid)
        elif o["type"] == "source_match" and not o.get("passed"):
            s.source_failed = True
            ref("source_failed", fid)
        explanation = o.get("explanation", "")
        if not o.get("passed") and explanation.startswith("Field '") and "is missing from the output" in explanation:
            s.format_failed = True  # the output lacks the field an answer evaluator reads: a format problem first
            ref("format_failed", fid)
    if case["execution_error"]:
        err = case["execution_error"]
        s.execution_error = True
        ref("execution_error", facts.add("execution_error", f"The task raised {err.get('type', 'an error')}: {_preview(err.get('message', ''))}"))

    # 2. Trace availability.
    trace_id = case["trace_id"] if trace_found else None
    s.has_trace = trace_found
    s.expected_sources = list(dict.fromkeys(case["expected_sources"]))
    if case["trace_id"] is None:
        ref("no_trace", facts.add("evidence_gap", "No trace was submitted with this result, so the execution cannot be inspected."))
        missing.append("A trace of this case's execution (submit trace_id with the result).")
    elif not trace_found:
        ref("no_trace", facts.add("evidence_gap", "The trace id submitted with this result does not exist in this project; it is not used as evidence."))
        missing.append("The submitted trace was never ingested (check that telemetry was flushed before finishing the run).")
    else:
        kinds: dict[str, int] = {}
        for sp in spans:
            kinds[sp["kind"]] = kinds.get(sp["kind"], 0) + 1
        summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "no spans"
        facts.add("trace", f"Linked trace has {len(spans)} span(s): {summary}.", trace_id=trace_id)
        if truncated:
            missing.append(f"Only the first {MAX_SPANS_PER_TRACE} spans of the trace were analyzed.")

    answer = _answer_value(case)
    answer_words = content_words(answer) if answer is not None else set()

    # 3. Retrieval spans, documents, and expected sources.
    retrieved: list[tuple[str, dict[str, Any], str]] = []  # (source, document, span id)
    for sp in spans:
        docs = sp["attributes"].get("documents") if isinstance(sp["attributes"], dict) else None
        if sp["kind"] != "retrieval" or not isinstance(docs, list):
            continue
        s.retrieval_span_count += 1
        docs = [d for d in docs if isinstance(d, dict)]
        listed = ", ".join(f"{d.get('source') or d.get('id')} (score {d.get('score')})" for d in docs[:8]) or "none"
        fid = facts.add("retrieval", f"Retrieval span {_preview(sp['name'], 60)} returned {len(docs)} document(s): {listed}.", trace_id, sp["id"])
        ref("retrieval_spans", fid)
        if not docs:
            ref("retrieval_empty", fid)
        for d in docs:
            retrieved.append((str(d.get("source") or d.get("id")), d, sp["id"]))
    s.retrieved_document_count = len(retrieved)
    if trace_found and s.expected_sources and s.retrieval_span_count == 0:
        missing.append("The trace has no retrieval span with documents, so retrieved sources cannot be checked.")
    for source in s.expected_sources:
        rank = next((i + 1 for i, (src, d, _) in enumerate(retrieved) if source in (src, d.get("id"))), None)
        if rank is not None:
            span_id = retrieved[rank - 1][2]
            s.retrieved_expected[source] = rank
            ref("expected_retrieved", facts.add("retrieval", f"Expected source {source} was retrieved (rank {rank} of {len(retrieved)}).", trace_id, span_id))
        elif trace_found and s.retrieval_span_count:
            s.missing_sources.append(source)
            ref("expected_missing", facts.add("retrieval", f"Expected source {source} was not returned by any retrieval span.", trace_id,
                                              next((sp["id"] for sp in spans if sp["kind"] == "retrieval"), None)))

    # 4. Grounding: how much of the answer appears in each retrieved document.
    with_content = [(src, d, sid) for src, d, sid in retrieved if isinstance(d.get("content"), str) and d["content"].strip()]
    s.documents_have_content = bool(with_content)
    if retrieved and not with_content:
        ref("no_content", facts.add("evidence_gap", "Retrieved documents carry no content, so grounding of the answer cannot be measured.", trace_id))
        missing.append("Document content on retrieval spans (pass `content` to add_documents).")
    if with_content and answer_words:
        scored = sorted(((overlap(answer_words, content_words(d["content"])), src, sid) for src, d, sid in with_content), reverse=True)
        best_score, best_src, best_sid = scored[0]
        s.best_document, s.best_document_overlap = best_src, best_score
        for score, src, sid in scored[:5]:
            fid = facts.add("comparison", f"{score:.0%} of the answer's content words occur in document {src}.", trace_id, sid)
            ref("best_document" if src == best_src else "other_document", fid)
            if src in s.expected_sources or src in s.retrieved_expected:
                s.expected_document_overlap = score if s.expected_document_overlap is None else max(s.expected_document_overlap, score)
                ref("expected_document", fid)

    # 5. Tools and model calls.
    for sp in spans:
        if sp["kind"] == "tool":
            s.tool_span_count += 1
            tool = sp["attributes"].get("tool", {}) if isinstance(sp["attributes"], dict) else {}
            label = f"Tool span {_preview(sp['name'], 60)}"
            if isinstance(tool, dict) and tool.get("name"):
                label += f" (tool {_preview(tool['name'], 60)}, arguments {_preview(tool.get('arguments'), 80)})"
            if sp["status"] == "error" or sp["error"]:
                err = sp["error"] or {}
                fid = facts.add("tool", f"{label} failed with {err.get('type', 'an error')}: {_preview(err.get('message', ''))}.", trace_id, sp["id"])
                s.tool_error_span_ids.append(sp["id"])
                ref("tool_error", fid)
            else:
                fid = facts.add("tool", f"{label} succeeded; output {_preview(sp['output'])}.", trace_id, sp["id"])
                ref("tool_ok", fid)
                if answer_words and sp["output"] is not None:
                    score = overlap(answer_words, content_words(sp["output"]))
                    if score >= 0.3:
                        ref("answer_from_tool", facts.add("comparison", f"{score:.0%} of the answer's content words occur in the output of tool span {_preview(sp['name'], 60)}.", trace_id, sp["id"]))
        elif sp["kind"] == "llm":
            fid = facts.add("llm", f"LLM span {_preview(sp['name'], 60)}" + (f" (model {_preview(sp['model'], 60)})" if sp["model"] else "")
                            + f" produced {_preview(sp['output'])}.", trace_id, sp["id"])
            ref("llm", fid)
            if sp["input"] is None:
                missing.append(f"The input (prompt) of LLM span {sp['name']!r} was not captured.")

    s.fact_refs = refs
    return CaseEvidence(result_id=case["result_id"], case_key=case["case_key"], trace_id=trace_id,
                        facts=facts.items, signals=s, missing_evidence=list(dict.fromkeys(missing)))
