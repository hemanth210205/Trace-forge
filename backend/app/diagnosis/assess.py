"""Server-side assessment of proposed hypotheses against the extracted evidence.

Whatever a provider claims, the evidence status, the trace/span references and the confidence ceiling are
decided here from deterministic signals. ``supported`` means the persisted evidence matches the category's
evidence pattern; it is *not* experimental validation (``validation_status`` stays ``unvalidated`` in M6).
"""

from app.diagnosis.schemas import AssessedHypothesis, CaseEvidence, EvidenceStatus, ProposedHypothesis, Signals

CONFIDENCE_CEILING: dict[str, float | None] = {"supported": 0.9, "unsupported": 0.5, "contradicted": 0.15, "inconclusive": None}
GROUNDED = 0.5  # answer/document lexical containment at which the answer is considered drawn from the document
UNGROUNDED = 0.3

DEFAULT_EXPERIMENTS = {
    "retrieval_failure": "Re-run this dataset version with a retriever change (e.g. stemming or embedding search) and compare source_match recall and answer evaluators against this run.",
    "context_selection_failure": "Re-run with the generator restricted to (or re-ranked towards) the expected source and check whether the failing answer evaluators pass.",
    "grounding_failure": "Re-run with an instruction to answer only from retrieved documents (and to say when they do not contain the answer); compare answer evaluators.",
    "tool_execution_failure": "Replay the failing tool call with the same arguments to reproduce the error, then re-run the case with the tool fixed or mocked.",
    "tool_selection_failure": "Re-run with the expected tool forced or described more precisely and compare the case outcome.",
    "reasoning_failure": "Re-run with the same retrieved context and a revised answer prompt (or a stronger model) to isolate synthesis from retrieval.",
    "output_format_failure": "Re-run with structured output enforcement for the required fields and check the format evaluators.",
    "insufficient_evidence": "Re-run the case with tracing enabled (and document content captured) so a diagnosis can inspect the execution.",
}


def _refs(s: Signals, *names: str) -> list[str]:
    out: list[str] = []
    for name in names:
        out += s.fact_refs.get(name, [])
    return out


def _expected_best(s: Signals) -> bool:
    return s.best_document is not None and (s.best_document in s.expected_sources or s.best_document in s.retrieved_expected)


def evaluate_category(category: str, s: Signals) -> tuple[EvidenceStatus, list[str], list[str], str]:
    """(evidence status, supporting fact ids, contradicting fact ids, uncertainty note)."""
    if category == "insufficient_evidence":
        return "inconclusive", _refs(s, "no_trace", "no_content"), [], "This states a gap in the evidence, not a cause."
    if not s.has_trace:
        return "inconclusive", _refs(s, "no_trace"), [], "No trace is linked to this case, so execution-level causes cannot be checked."

    if category == "retrieval_failure":
        if not s.expected_sources or s.retrieval_span_count == 0:
            return "inconclusive", [], [], "The case defines no expected sources or the trace has no retrieval spans."
        if s.missing_sources:
            note = ""
            if not s.answer_failed and s.answer_passed_any:
                note = "Answer evaluators passed: the retrieval gap explains the source check failure, not an incorrect answer."
            return "supported", _refs(s, "expected_missing", "retrieval_empty", "source_failed"), _refs(s, "answer_passed"), note
        return "contradicted", [], _refs(s, "expected_retrieved"), "Every expected source was retrieved."

    if category == "context_selection_failure":
        if not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "The answer evaluators passed."
        if not s.retrieved_expected:
            return "contradicted", [], _refs(s, "expected_missing"), "The expected source was not retrieved, so it could not be selected."
        if not s.documents_have_content:
            return "inconclusive", _refs(s, "expected_retrieved"), [], "Document content was not captured; which document the answer used is unknown."
        if _expected_best(s):
            return "contradicted", [], _refs(s, "best_document"), "The answer is closest to an expected source."
        if (s.best_document_overlap or 0) >= GROUNDED and (s.expected_document_overlap or 0) < (s.best_document_overlap or 0):
            return ("supported", _refs(s, "answer_failed", "expected_retrieved", "best_document"), [],
                    "Document attribution is lexical; paraphrased use of a document may be missed.")
        return "unsupported", _refs(s, "expected_retrieved"), [], "The answer is not clearly drawn from any one retrieved document."

    if category == "grounding_failure":
        if not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "The answer evaluators passed."
        if not s.documents_have_content:
            return "inconclusive", [], _refs(s, "no_content"), "Document content was not captured, so grounding cannot be measured."
        best = s.best_document_overlap or 0
        if best < UNGROUNDED:
            return "supported", _refs(s, "answer_failed", "best_document", "other_document"), [], "Lexical overlap may understate paraphrased grounding."
        if best >= GROUNDED:
            return "contradicted", [], _refs(s, "best_document"), "The answer closely matches a retrieved document."
        return "unsupported", _refs(s, "best_document"), [], "Partial overlap with retrieved documents."

    if category == "tool_execution_failure":
        if s.tool_span_count == 0:
            return "contradicted", [], [], "No tool was called in the trace."
        if s.tool_error_span_ids and s.answer_failed:
            return "supported", _refs(s, "tool_error", "answer_failed"), [], ""
        if s.tool_error_span_ids:
            return "contradicted", _refs(s, "tool_error"), _refs(s, "answer_passed"), "A tool failed but the agent recovered: the answer evaluators passed."
        return "contradicted", [], _refs(s, "tool_ok"), "Every tool call succeeded."

    if category == "tool_selection_failure":
        if s.tool_span_count == 0 and not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "No tool was needed for a passing answer."
        return "unsupported", _refs(s, "tool_ok", "tool_error"), [], "Whether a different tool should have been used needs a semantic judgement; test it with an experiment."

    if category == "reasoning_failure":
        if not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "The answer evaluators passed."
        if s.documents_have_content and _expected_best(s) and (s.best_document_overlap or 0) >= GROUNDED:
            return "supported", _refs(s, "answer_failed", "best_document"), [], "The right document was used, yet the answer is wrong."
        return "unsupported", _refs(s, "answer_failed"), [], "Synthesis problems cannot be separated from context problems with this evidence."

    if category == "output_format_failure":
        if s.format_failed:
            return "supported", _refs(s, "format_failed"), [], ""
        return ("contradicted" if _refs(s, "format_passed") else "unsupported"), [], _refs(s, "format_passed"), ""
    return "unsupported", [], [], ""


def assess(proposal: ProposedHypothesis, evidence: CaseEvidence) -> tuple[AssessedHypothesis, int]:
    """Returns the assessed hypothesis and how many provider-cited fact ids did not exist (and were dropped)."""
    known = {f.id: f for f in evidence.facts}
    cited_support = [i for i in proposal.supporting_fact_ids if i in known]
    cited_contra = [i for i in proposal.contradicting_fact_ids if i in known]
    discarded = len(proposal.supporting_fact_ids) + len(proposal.contradicting_fact_ids) - len(cited_support) - len(cited_contra)

    status, auto_support, auto_contra, note = evaluate_category(proposal.category, evidence.signals)
    contradicting = list(dict.fromkeys(auto_contra + cited_contra))
    supporting = [i for i in dict.fromkeys(auto_support + cited_support) if i not in contradicting and i in known]
    contradicting = [i for i in contradicting if i in known]
    cited = [known[i] for i in supporting + contradicting]

    ceiling = CONFIDENCE_CEILING[status]
    confidence = None if ceiling is None else round(min(proposal.confidence, ceiling), 3)
    uncertainty = " ".join(part for part in (note, proposal.uncertainty.strip()) if part)
    return (
        AssessedHypothesis(
            category=proposal.category,
            explanation=proposal.explanation.strip(),
            supporting_facts=supporting,
            contradicting_facts=contradicting,
            evidence_trace_ids=list(dict.fromkeys(f.trace_id for f in cited if f.trace_id)),
            evidence_span_ids=list(dict.fromkeys(f.span_id for f in cited if f.span_id)),
            evidence_status=status,
            confidence=confidence,
            uncertainty=uncertainty,
            missing_evidence=list(dict.fromkeys([*proposal.missing_evidence, *evidence.missing_evidence]))[:10],
            recommended_experiment=proposal.recommended_experiment.strip() or DEFAULT_EXPERIMENTS[proposal.category],
        ),
        discarded,
    )
