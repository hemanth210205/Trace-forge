"""Server-side assessment of proposed hypotheses against the extracted evidence.

Whatever a provider claims, the evidence status, the trace/span references and the confidence ceiling are
decided here from deterministic signals. ``supported`` means the persisted evidence matches the category's
evidence pattern; it is *not* experimental validation (``validation_status`` stays ``unvalidated`` in M6).
"""

from app.diagnosis.schemas import AssessedHypothesis, CaseEvidence, EvidenceStatus, ProposedHypothesis, Signals

CONFIDENCE_CEILING: dict[str, float | None] = {"supported": 0.9, "unsupported": 0.5, "contradicted": 0.15, "inconclusive": None}

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
        if not s.answer_available:
            return "inconclusive", _refs(s, "format_failed"), [], "The answer field is missing, so which document the answer used cannot be checked."
        if s.claims_supported_by_expected:
            return "contradicted", [], _refs(s, "claim_supported_expected"), "Claims in the answer are supported by an expected source."
        if s.claims_supported_by_other_document:
            return ("supported", _refs(s, "answer_failed", "expected_retrieved", "claim_supported_other"), [],
                    "Claim attribution is lexical; a paraphrased use of the expected source could be missed.")
        return "unsupported", _refs(s, "expected_retrieved"), _refs(s, "claim_contradicted"), "No claim in the answer is attributed to a non-expected retrieved document."

    if category == "grounding_failure":
        if not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "The answer evaluators passed."
        if not s.answer_available:
            return "inconclusive", _refs(s, "format_failed"), [], "The answer field is missing, so its grounding cannot be checked."
        if not s.documents_have_content and not s.tool_evidence:
            return "inconclusive", [], _refs(s, "no_content"), "Document content was not captured, so grounding cannot be measured."
        if s.claims_unsupported:
            return ("supported", _refs(s, "claim_unsupported", "answer_failed"), _refs(s, "claim_supported"),
                    "Missing evidence: the answer asserts values or terms found in no retrieved document or tool result.")
        if s.claims_contradicted:
            return ("contradicted", [], _refs(s, "claim_contradicted"),
                    "The evidence addresses the answer's claims and contradicts them: contradictory evidence, not missing evidence.")
        factual = s.claims_supported + s.claims_undetermined
        if s.claims_supported and not s.claims_undetermined:
            return "contradicted", [], _refs(s, "claim_supported"), "Every factual claim is attributed to a retrieved document or tool result."
        if not factual and s.claims_abstention:
            return "contradicted", [], _refs(s, "claim_abstention"), "The answer makes no factual claim; it reports missing information or an error."
        return "inconclusive", _refs(s, "claim_undetermined"), _refs(s, "claim_supported"), "The answer's claims could neither be attributed to nor refuted by the evidence."

    if category == "tool_execution_failure":
        if s.tool_span_count == 0:
            return "contradicted", [], [], "No tool was called in the trace."
        if s.tool_error_span_ids and s.answer_failed:
            return "supported", _refs(s, "tool_error", "answer_failed"), [], ""
        if s.tool_error_span_ids:
            return "contradicted", _refs(s, "tool_error"), _refs(s, "answer_passed"), "A tool failed but the agent recovered: the answer evaluators passed."
        return "contradicted", [], _refs(s, "tool_ok"), "Every tool call succeeded."

    if category == "tool_selection_failure":
        if s.tool_selection_mismatch and s.answer_failed:
            return "supported", _refs(s, "tool_selection_mismatch", "answer_failed"), [], "Direct evidence: expected tool was not invoked in trace."
        if s.tool_selection_matched and not s.tool_selection_ambiguous:
            return "contradicted", [], _refs(s, "tool_selection_matched"), "The expected tool was called without extraneous tools."
        if s.tool_selection_ambiguous:
            return "inconclusive", _refs(s, "tool_selection_ambiguous"), [], "Expected tool was called, but unexpected tools were also invoked."
        if s.tool_span_count == 0 and not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "No tool was needed for a passing answer."
        return "unsupported", _refs(s, "tool_ok", "tool_error"), [], "Whether a different tool should have been used needs a semantic judgement; test it with an experiment."

    if category == "reasoning_failure":
        # Reasoning is attributed only when the trace shows the right information reached the agent.
        if not s.answer_failed:
            return "contradicted", [], _refs(s, "answer_passed"), "The answer evaluators passed."
        if not s.answer_available:
            return "inconclusive", _refs(s, "format_failed"), [], "The answer field is missing, so the answer's reasoning cannot be checked."
        if s.claims_contradicted:
            return ("supported", _refs(s, "claim_contradicted", "answer_failed"), [],
                    "Evidence available to the agent contradicts the answer: the conclusion disagrees with the evidence.")
        if s.missing_sources or s.tool_error_span_ids or s.tool_selection_mismatch:
            return ("unsupported", [], _refs(s, "expected_missing", "tool_error", "tool_selection_mismatch"),
                    "A retrieval or tool problem explains the wrong answer without a reasoning error.")
        if s.claims_unsupported:
            return ("contradicted", [], _refs(s, "claim_unsupported"),
                    "The answer asserts things absent from the evidence (missing evidence), not a wrong conclusion from available evidence.")
        if s.claims_supported_by_expected and not s.claims_supported_by_other_document:
            return ("supported", _refs(s, "answer_failed", "claim_supported_expected"), [],
                    "The answer is built from the expected source yet fails the answer evaluators.")
        return "unsupported", _refs(s, "answer_failed"), [], "No evidence shows that correct information reached the agent and was misused."

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
