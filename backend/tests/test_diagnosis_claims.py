"""M10: claim-level grounding and reasoning diagnosis.

Part 1 reproduces the *shapes* of the M9 misdiagnoses (answer from a tool, echoed tool error, fabricated number with
partial word overlap, missing answer field, answer contradicting a retrieved document, off-topic claim with no
evidence). Part 2 uses an unrelated policy domain (subscriptions), so the rules are not tied to the support agent's
wording. Nothing here reads the holdout dataset.
"""

from typing import Any

from app.diagnosis import claims
from app.diagnosis.assess import evaluate_category
from app.diagnosis.evidence import build_case_evidence
from app.diagnosis.providers import MockProvider
from app.diagnosis.assess import assess

RETURNS = (
    "# Returns Policy\n\n## Return Period\nEligible items may be returned within 30 days of the delivery date.\n\n"
    "## Refunds\nApproved returns are refunded to the original payment method.\n\n"
    "## Non-Returnable Products\nThe following items are strictly non-returnable:\n- Gift cards\n- Opened software\n"
)
WARRANTY = (
    "# Warranty Policy\n\nAll devices come with a 1-year limited warranty.\nThis warranty covers manufacturing defects.\n\n"
    "## Exclusions\nThis warranty does not cover:\n- Accidental damage, including drops and cracked screens\n- Liquid spills\n"
)
SUBSCRIPTIONS = (
    "# Subscription Terms\n\nMonthly plans renew automatically every 30 days.\nAnnual plans cost $99 and renew every 12 months.\n"
    "You may cancel at any time from the account settings page.\nCancelled plans stay active until the end of the paid period.\n\n"
    "## Refunds\nAnnual plans cancelled within 14 days receive a full refund.\nMonthly plans are not refunded after renewal.\n"
)
BILLING = "# Billing\n\nInvoices are emailed on the first business day of each month.\nPayments are accepted by card or bank transfer.\n"
DOCS = {"kb/returns.md": RETURNS, "kb/warranty.md": WARRANTY, "kb/subscriptions.md": SUBSCRIPTIONS, "kb/billing.md": BILLING}


def outcome(type_: str, passed: bool, expected: Any = None, actual: Any = None, explanation: str = "x") -> dict:
    return {"evaluator": type_, "type": type_, "applicable": True, "passed": passed, "score": 1.0 if passed else 0.0,
            "expected": expected, "actual": actual, "explanation": explanation, "supporting_refs": []}


def failed_answer(answer: str, sources: list[str], extra: list[dict] | None = None) -> dict:
    evaluations = [outcome("normalized_match", False, "the expected answer", answer), *(extra or [])]
    return {"result_id": "r1", "case_key": "k", "input": {"question": "?"}, "expected_output": "x", "expected_sources": sources,
            "metadata": {}, "output": {"answer": answer}, "execution_error": None, "trace_id": "t1", "evaluations": evaluations}


def retrieval(docs: list[str], span_id: str = "s-ret") -> dict:
    documents = [{"id": d, "source": d, "score": 0.5, "content": DOCS[d]} for d in docs]
    return {"id": span_id, "name": "kb-search", "kind": "retrieval", "status": "ok", "input": "q", "output": None,
            "attributes": {"documents": documents}, "error": None, "model": None}


def tool(name: str, output: Any = None, error: dict | None = None, span_id: str = "s-tool") -> dict:
    return {"id": span_id, "name": name, "kind": "tool", "status": "error" if error else "ok", "input": {}, "output": output,
            "attributes": {"tool": {"name": name, "arguments": {}}}, "error": error, "model": None}


def llm() -> dict:
    return {"id": "s-llm", "name": "compose", "kind": "llm", "status": "ok", "input": {"prompt": "p"}, "output": "out",
            "attributes": {}, "error": None, "model": "m"}


def statuses(case: dict, spans: list[dict]) -> dict[str, str]:
    signals = build_case_evidence(case, True, spans, False).signals
    return {c: evaluate_category(c, signals)[0] for c in ("grounding_failure", "reasoning_failure", "context_selection_failure")}


def proposed(case: dict, spans: list[dict]) -> dict[str, Any]:
    evidence = build_case_evidence(case, True, spans, False)
    return {a.category: a for a in (assess(h, evidence)[0] for h in MockProvider().propose(evidence).hypotheses)}


# --- part 1: M9 mistake shapes --------------------------------------------------------------------------------------


def test_answer_taken_from_a_tool_result_is_not_a_grounding_failure() -> None:  # M9 case-03
    c = failed_answer("Calculated refund amount is $100.00 (eligible).", ["kb/returns.md"])
    spans = [retrieval(["kb/warranty.md"]), tool("refund-calculator", {"refund_amount": 100.0, "eligibility_status": "eligible"}), llm()]
    assert statuses(c, spans)["grounding_failure"] == "contradicted"
    facts = build_case_evidence(c, True, spans, False).facts
    assert any("supported by tool refund-calculator" in f.statement and f.span_id == "s-tool" for f in facts)


def test_answer_echoing_a_tool_error_is_not_a_grounding_failure() -> None:  # M9 case-07, case-44
    c = failed_answer("I encountered an error looking up that information: connection timeout for ORD-9.", [])
    spans = [retrieval(["kb/returns.md"]), tool("order-lookup", error={"type": "RuntimeError", "message": "connection timeout for ORD-9"}), llm()]
    result = proposed(c, spans)
    assert "grounding_failure" not in result or result["grounding_failure"].evidence_status != "supported"
    assert statuses(c, spans)["grounding_failure"] == "contradicted"


def test_fabricated_number_is_unsupported_even_with_partial_word_overlap() -> None:  # M9 case-09/45/46
    c = failed_answer("You can return items within 365 days by visiting our secret downtown warehouse.", ["kb/returns.md"])
    result = proposed(c, [retrieval(["kb/returns.md", "kb/warranty.md"]), llm()])
    assert result["grounding_failure"].evidence_status == "supported"
    assert "reasoning_failure" not in result or result["reasoning_failure"].evidence_status != "supported"
    facts = build_case_evidence(c, True, [retrieval(["kb/returns.md"])], False).facts
    assert any("365" in f.statement and "occur in no evidence" in f.statement for f in facts)


def test_missing_answer_field_makes_content_categories_inconclusive() -> None:  # M9 case-10, case-47
    c = failed_answer("", ["kb/returns.md"], [outcome("required_fields", False, explanation="Missing or empty: answer")])
    c["evaluations"][0] = outcome("normalized_match", False, "x", None, "Field 'answer' is missing from the output.")
    c["output"] = {"reply_text": "The warranty covers manufacturing defects.", "status": "incomplete"}
    spans = [retrieval(["kb/warranty.md", "kb/returns.md"]), llm()]
    assert statuses(c, spans) == {"grounding_failure": "inconclusive", "reasoning_failure": "inconclusive",
                                  "context_selection_failure": "inconclusive"}
    result = proposed(c, spans)
    assert result["output_format_failure"].evidence_status == "supported"
    assert not any(h.evidence_status == "supported" for k, h in result.items() if k != "output_format_failure")


def test_answer_contradicting_a_retrieved_document_is_reasoning_not_context_selection() -> None:  # M9 case-05, case-41
    c = failed_answer("Yes, the warranty covers accidental damage and drops without exception.", ["kb/returns.md"])
    spans = [retrieval(["kb/returns.md", "kb/warranty.md"]), llm()]
    assert statuses(c, spans) == {"grounding_failure": "contradicted", "reasoning_failure": "supported",
                                  "context_selection_failure": "unsupported"}
    hyp = proposed(c, spans)["reasoning_failure"]
    assert hyp.evidence_status == "supported" and hyp.evidence_span_ids == ["s-ret"]


def test_off_topic_claim_without_any_evidence_is_grounding_not_reasoning() -> None:  # M9 case-42
    c = failed_answer("Yes, our warranty covers accidental damage and liquid drops.", ["kb/returns.md"])
    spans = [retrieval(["kb/returns.md", "kb/billing.md"]), llm()]
    s = statuses(c, spans)
    assert s["grounding_failure"] == "supported" and s["reasoning_failure"] == "contradicted"


# --- part 2: independent scenarios (subscription domain) ------------------------------------------------------------


def test_correct_paraphrase_with_low_overlap_is_not_a_grounding_failure() -> None:
    c = failed_answer("You get your money back on a yearly plan if you cancel within two weeks.", ["kb/subscriptions.md"])
    s = statuses(c, [retrieval(["kb/subscriptions.md"]), llm()])
    assert s["grounding_failure"] != "supported" and s["reasoning_failure"] != "supported"


def test_known_limitation_a_paraphrase_sharing_no_vocabulary_looks_fabricated() -> None:
    # Documents a limitation instead of hiding it: lexical attribution cannot tell a paraphrase with no shared
    # terms from a hallucination. If a semantic matcher is added, this test should be inverted.
    c = failed_answer("Yearly memberships can be fully reimbursed if stopped inside a fortnight.", ["kb/subscriptions.md"])
    assert statuses(c, [retrieval(["kb/subscriptions.md"]), llm()])["grounding_failure"] == "supported"


def test_value_from_another_clause_of_the_right_document_is_a_reasoning_failure() -> None:
    c = failed_answer("Annual plans cancelled within 30 days receive a full refund.", ["kb/subscriptions.md"])
    s = statuses(c, [retrieval(["kb/subscriptions.md", "kb/billing.md"]), llm()])
    assert s["reasoning_failure"] == "supported" and s["grounding_failure"] == "contradicted"


def test_negation_flip_of_the_right_document_is_a_reasoning_failure() -> None:
    c = failed_answer("Monthly plans are refunded after renewal.", ["kb/subscriptions.md"])
    assert statuses(c, [retrieval(["kb/subscriptions.md"]), llm()])["reasoning_failure"] == "supported"


def test_claim_supported_by_another_retrieved_document_is_not_ungrounded() -> None:
    c = failed_answer("Invoices are emailed on the first business day of each month.", ["kb/subscriptions.md"])
    spans = [retrieval(["kb/subscriptions.md", "kb/billing.md"]), llm()]
    s = statuses(c, spans)
    assert s["grounding_failure"] == "contradicted" and s["context_selection_failure"] == "supported"
    assert s["reasoning_failure"] != "supported"


def test_claims_split_across_two_expected_documents_are_grounded() -> None:
    c = failed_answer("Annual plans cost $99. Invoices are emailed on the first business day of each month.",
                      ["kb/subscriptions.md", "kb/billing.md"])
    s = statuses(c, [retrieval(["kb/subscriptions.md", "kb/billing.md"]), llm()])
    assert s["grounding_failure"] == "contradicted" and s["context_selection_failure"] == "contradicted"


def test_answer_without_any_evidence_is_inconclusive_for_grounding() -> None:
    c = failed_answer("Annual plans cost $99.", ["kb/subscriptions.md"])
    empty = {**retrieval([]), "attributes": {"documents": []}}
    s = statuses(c, [empty, llm()])
    assert s["grounding_failure"] == "inconclusive" and s["reasoning_failure"] != "supported"


def test_abstention_is_neither_hallucination_nor_reasoning_failure() -> None:
    c = failed_answer("I am sorry, but the documentation does not contain details about family plans.", ["kb/subscriptions.md"])
    s = statuses(c, [retrieval(["kb/subscriptions.md"]), llm()])
    assert s["grounding_failure"] == "contradicted" and s["reasoning_failure"] == "unsupported"


def test_passing_answer_is_never_a_content_failure() -> None:
    c = failed_answer("Annual plans cost $99.", ["kb/subscriptions.md"])
    c["evaluations"] = [outcome("normalized_match", True), outcome("source_match", False)]
    s = statuses(c, [retrieval(["kb/subscriptions.md"]), llm()])
    assert set(s.values()) == {"contradicted"}


# --- claim module units ---------------------------------------------------------------------------------------------


def test_section_numbers_in_headings_are_not_policy_values() -> None:
    units = claims.document_units("kb/x.md", "## 6. Tracking Information\nUpdates appear within 12 hours.", "s", False)
    assert units[0].text == "Tracking Information" and not units[0].numbers


def test_list_items_inherit_the_polarity_of_their_lead_in() -> None:
    units = claims.document_units("kb/warranty.md", WARRANTY, "s", True)
    spill = next(u for u in units if "Liquid" in u.text)
    assert spill.negated and "does not cover" in spill.text


def test_numbers_are_compared_by_value_and_contractions_negate() -> None:
    _, numbers, _ = claims.tokens("$100.00 refund")
    assert numbers == claims.tokens("refund_amount 100.0")[1] == {"100"}
    assert claims.tokens("Monthly plans aren't refunded")[2] is True
    assert claims.parse("two weeks")[1] == [("2", "14")]  # a converted unit, not two different values
    assert claims.covered(claims.parse("within two weeks")[1], {"14", "30"})


def test_unrelated_short_text_and_compound_answers_are_split_into_claims() -> None:
    parts = claims.split_claims("The warranty covers defects for 1 year and strictly does not cover drops. Refunds go to the card.")
    assert parts == ["The warranty covers defects for 1 year", "strictly does not cover drops", "Refunds go to the card"]
    assert claims.split_claims("Liquid immersion and spills are excluded") == ["Liquid immersion and spills are excluded"]
