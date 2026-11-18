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

from app.diagnosis import claims as claims_mod
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
            "expected_sources": list(c.expected_sources or []), "metadata": dict(c.metadata_ or {}),
            "output": r.output, "execution_error": r.execution_error,
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


def _answer_field_missing(case: dict[str, Any]) -> bool:
    """True when an answer evaluator could not read its field: the answer content was never evaluated, so the rest
    of the output must not be treated as the answer (that turns a format failure into a content hypothesis)."""
    return any(
        o.get("type") in ANSWER_EVALUATORS and o.get("applicable") and not o.get("passed")
        and str(o.get("explanation", "")).startswith("Field '") and "is missing from the output" in str(o.get("explanation", ""))
        for o in case["evaluations"]
    )


def _answer_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("answer", "output", "text", "response"):
            if isinstance(value.get(key), str):
                return value[key]
    return json.dumps(value, ensure_ascii=False, default=str)


class _Facts:
    def __init__(self) -> None:
        self.items: list[Fact] = []

    def add(self, kind: str, statement: str, trace_id: str | None = None, span_id: str | None = None) -> str:
        fact_id = f"F{len(self.items) + 1}"
        if len(self.items) < MAX_FACTS_PER_CASE:
            self.items.append(Fact(id=fact_id, kind=kind, statement=statement, trace_id=trace_id, span_id=span_id))  # type: ignore[arg-type]
        return fact_id


def _attribute_claims(answer: Any, with_content: list[tuple[str, dict[str, Any], str]], spans: list[dict[str, Any]],
                      s: Signals, facts: "_Facts", ref: Any, trace_id: str | None) -> None:
    text = _answer_text(answer)
    if not text or not text.strip():
        return
    units: list[claims_mod.Unit] = []
    for src, d, sid in with_content:
        expected = src in s.expected_sources or d.get("id") in s.expected_sources
        units += claims_mod.document_units(src, d["content"], sid, expected)
    for sp in spans:
        if sp["kind"] != "tool":
            continue
        tool = sp["attributes"].get("tool", {}) if isinstance(sp["attributes"], dict) else {}
        name = tool.get("name") if isinstance(tool, dict) and tool.get("name") else sp["name"]
        tool_unit_list = claims_mod.tool_units(str(name), sp["output"], sp["error"], sp["id"])
        s.tool_evidence |= bool(tool_unit_list)
        units += tool_unit_list
    if not units:
        return
    attributed = claims_mod.attribute(text, units)
    for c in attributed:
        quoted = _preview(c.text, 120)
        if c.status in ("supported", "contradicted"):
            where = f"{'tool' if c.evidence_kind != 'document' else 'document'} {c.source}"
            verb = "is supported by" if c.status == "supported" else "is contradicted by"
            statement = f"Claim {c.index} {quoted} {verb} {where} ({c.reason}; evidence {_preview(c.evidence_text, 140)})."
            fid = facts.add("comparison", statement, trace_id, c.span_id)
            if c.status == "supported":
                s.claims_supported += 1
                s.supporting_sources = list(dict.fromkeys([*s.supporting_sources, str(c.source)]))
                ref("claim_supported", fid)
                if c.evidence_kind == "document":
                    ref("claim_supported_expected" if c.expected_source else "claim_supported_other", fid)
                    s.claims_supported_by_expected += int(c.expected_source)
                    s.claims_supported_by_other_document += int(not c.expected_source)
                else:
                    s.claims_supported_by_tool += 1
            else:
                s.claims_contradicted += 1
                s.contradicting_sources = list(dict.fromkeys([*s.contradicting_sources, str(c.source)]))
                ref("claim_contradicted", fid)
        elif c.status == "unsupported":
            detail = []
            if c.novel_numbers:
                detail.append("value(s) " + ", ".join(c.novel_numbers[:5]))
            if c.novel_terms:
                detail.append("term(s) " + ", ".join(json.dumps(t) for t in c.novel_terms[:6]))
            statement = f"Claim {c.index} {quoted} is not supported by any retrieved document or tool result: " + " and ".join(detail) + " occur in no evidence."
            s.claims_unsupported += 1
            ref("claim_unsupported", facts.add("comparison", statement, trace_id))
        elif c.status == "abstention":
            s.claims_abstention += 1
            ref("claim_abstention", facts.add("comparison", f"Claim {c.index} {quoted} reports missing information or an error rather than asserting a fact.", trace_id))
        else:
            s.claims_undetermined += 1
            ref("claim_undetermined", facts.add("comparison", f"Claim {c.index} {quoted} could not be attributed to or refuted by the evidence ({c.reason}).", trace_id))
    s.claim_count = len(attributed)


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

    s.answer_available = not _answer_field_missing(case) and not case["execution_error"]
    answer = _answer_value(case) if s.answer_available else None
    answer_words = content_words(answer) if answer is not None else set()
    if not s.answer_available and not case["execution_error"]:
        missing.append("The answer field was missing from the output, so the answer's content could not be checked against evidence.")

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
    tool_names_called = []
    for sp in spans:
        if sp["kind"] == "tool":
            s.tool_span_count += 1
            tool = sp["attributes"].get("tool", {}) if isinstance(sp["attributes"], dict) else {}
            tool_name = tool.get("name") if isinstance(tool, dict) and tool.get("name") else sp.get("name")
            if tool_name:
                tool_names_called.append(tool_name)
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

    s.actual_tools = tool_names_called
    meta = case.get("metadata", {}) or {}
    expected_tool = meta.get("expected_tool")
    if expected_tool:
        s.expected_tool = expected_tool
        has_expected = expected_tool in tool_names_called
        unexpected_tools = [t for t in tool_names_called if t != expected_tool]
        if not has_expected:
            s.tool_selection_mismatch = True
            fid = facts.add(
                "tool",
                f"Expected tool '{expected_tool}' was not called. Actual tool(s) called: {tool_names_called or ['none']}.",
                trace_id,
            )
            ref("tool_selection_mismatch", fid)
        elif unexpected_tools:
            # Both expected and unexpected tools were called
            s.tool_selection_ambiguous = True
            fid = facts.add(
                "tool",
                f"Expected tool '{expected_tool}' was called, but unexpected tool(s) were also invoked: {unexpected_tools}.",
                trace_id,
            )
            ref("tool_selection_ambiguous", fid)
        else:
            # Expected tool called and no unexpected tools
            s.tool_selection_matched = True
            fid = facts.add(
                "tool",
                f"Expected tool '{expected_tool}' was called without unexpected tools.",
                trace_id,
            )
            ref("tool_selection_matched", fid)

    # 6. Claim-level attribution of the answer to documents and tool results (see app.diagnosis.claims).
    _attribute_claims(answer, with_content, spans, s, facts, ref, trace_id)

    s.fact_refs = refs
    return CaseEvidence(result_id=case["result_id"], case_key=case["case_key"], trace_id=trace_id,
                        facts=facts.items, signals=s, missing_evidence=list(dict.fromkeys(missing)))
