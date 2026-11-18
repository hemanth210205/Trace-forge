"""M10-C multi-intent retrieval and the pre-registered contextual injection modes.

Queries here are development phrasings (dataset v3 or written for this file). The holdout dataset is never read.
"""

import json
import re
from pathlib import Path

import pytest

from agents.customer_support_agent.agent import (
    FABRICATED_ANSWERS,
    KNOWLEDGE_BASE,
    detect_intents,
    execute_agent_task,
    retrieve_documents,
)

MULTI = {"retriever_strategy": "multi_intent", "context_strategy": "multi_intent"}
BASELINE = {"retriever_strategy": "keyword", "context_strategy": "best_rank"}
V3 = json.loads((Path(__file__).resolve().parents[1] / "scenarios" / "test_cases.json").read_text(encoding="utf-8"))


def ask(question: str, config: dict = MULTI, mode: str = "normal") -> dict:
    return execute_agent_task({"question": question, "override_mode": mode}, config=config)


def intents(question: str) -> list[str]:
    return [i["intent"] for i in detect_intents(question)]


def sources(question: str) -> list[str]:
    return [d["source"] for d in retrieve_documents(question, k=2, strategy="multi_intent")]


# --- multi-intent combinations ---------------------------------------------------------------------------------


def test_warranty_and_returns() -> None:
    q = "Can I return a defective hardware unit after 45 days of delivery?"  # v3 case-32
    assert set(intents(q)) == {"returns_policy", "warranty_policy"}
    assert set(sources(q)) == {"kb/returns.md", "kb/warranty.md"}
    out = ask(q)
    assert "30 days" in out["answer"] and "manufacturing defects" in out["answer"]
    assert out["sources"] == ["kb/returns.md", "kb/warranty.md"]


def test_one_high_scoring_document_does_not_eliminate_the_other() -> None:
    q = "Can I return a defective hardware unit after 45 days of delivery?"
    # The single-intent weighted retriever lets returns.md swamp warranty.md (the M9 case-32 regression).
    weighted = retrieve_documents(q, k=2, strategy="intent_weighted")
    assert weighted[0]["source"] == "kb/returns.md" and weighted[1]["score"] < 0.5
    docs = retrieve_documents(q, k=2, strategy="multi_intent")
    assert {d["intent"] for d in docs} == {"returns_policy", "warranty_policy"}
    assert all(d["score"] >= 1.0 for d in docs)


def test_shipping_and_returns() -> None:
    q = "How much does express shipping cost and what is the refund window?"
    assert intents(q) == ["shipping_policy", "returns_policy"]  # in the order the question asks
    answer = ask(q)["answer"]
    assert "$14.99" in answer and "30 days" in answer


def test_warranty_and_shipping() -> None:
    q = "Does the warranty cover defects and how long does standard shipping take?"
    assert intents(q) == ["warranty_policy", "shipping_policy"]
    answer = ask(q)["answer"]
    assert "manufacturing defects" in answer and "3 to 5 business days" in answer


def test_three_intents_each_get_their_own_document() -> None:
    q = "What is the refund window, what does the warranty cover, and how fast is express shipping?"
    assert set(sources(q)) == {"kb/returns.md", "kb/warranty.md", "kb/shipping.md"}


# --- single intent, irrelevant, ambiguous ----------------------------------------------------------------------


def test_single_intent_query_retrieves_only_its_document() -> None:
    assert sources("How long does standard shipping take across the country?") == ["kb/shipping.md"]
    assert intents("What is protected under the one year limited hardware guarantee?") == ["warranty_policy"]


@pytest.mark.parametrize("case", [c for c in V3 if c["key"] != "case-32-cross-policy-warranty-returns"], ids=lambda c: c["key"])
def test_v3_answers_are_unchanged_for_single_intent_cases(case: dict) -> None:
    # Single-intent behaviour is preserved: outside case-32 every v3 case has the same exact-match outcome, and the
    # non-injected cases give the identical answer. Injected retrieval failures may be worded differently (the
    # multi-intent answer names the topic it could not find instead of answering from an irrelevant document).
    multi = execute_agent_task(dict(case["input"]), config=MULTI).get("answer")
    base = execute_agent_task(dict(case["input"]), config=BASELINE).get("answer")
    assert (multi == case["expected_output"]) == (base == case["expected_output"])
    if case["metadata"]["expected_failure"] is None:
        assert multi == base


def test_irrelevant_query_retrieves_nothing_and_says_so() -> None:
    q = "What is the weather forecast for tomorrow?"
    assert intents(q) == [] and sources(q) == []
    assert "does not contain sufficient details" in ask(q)["answer"]


def test_a_single_document_specific_word_is_not_an_intent() -> None:
    assert intents("Do you sell gift wrapping?") == []


def test_ambiguous_query_asks_for_clarification_without_retrieving() -> None:
    q = "Tell me about your policies."
    assert sources(q) == []
    answer = ask(q)["answer"]
    assert "clarify" in answer and all(t in answer for t in ("returns", "shipping", "warranty"))


def test_unanswerable_part_is_reported_and_the_rest_answered() -> None:
    answer = ask("Do you sell gift wrapping and how long does express shipping take?")["answer"]
    assert "1 to 2 business days" in answer
    assert 'could not find information about "Do you sell gift wrapping"' in answer


def test_a_clause_that_refers_back_is_not_unanswerable() -> None:
    answer = ask("How much is express shipping and how long does it take to deliver?")["answer"]
    assert "could not find" not in answer


def test_retrieval_failure_is_reported_per_intent() -> None:
    q = "Can I return a defective hardware unit after 45 days of delivery?"
    out = execute_agent_task({"question": q, "override_mode": "retrieval_failure_empty"}, config=MULTI)
    assert out["sources"] == []
    assert "returns policy information" in out["answer"] and "warranty policy information" in out["answer"]


def test_generation_and_selection_injections_behave_identically_under_multi_intent() -> None:
    q = "What items are covered under the standard warranty terms?"
    for mode in ("context_selection_failure", "reasoning_failure", "grounding_failure", "insufficient_evidence"):
        assert ask(q, MULTI, mode).get("answer") == ask(q, BASELINE, mode).get("answer"), mode
    assert "answer" not in ask(q, MULTI, "output_format_failure")


# --- pre-registered contextual injection modes ---------------------------------------------------------------------


def _kb_numbers() -> set[str]:
    return {n for d in KNOWLEDGE_BASE for n in re.findall(r"\d+(?:\.\d+)?", d["content"])}


def test_contextual_grounding_failure_uses_values_absent_from_every_document() -> None:
    kb = _kb_numbers()
    for answer in FABRICATED_ANSWERS.values():
        assert set(re.findall(r"\d+(?:\.\d+)?", answer)) - kb, answer
    out = ask("How long does standard shipping take across the country?", BASELINE, "grounding_failure_contextual")
    assert out["answer"] == FABRICATED_ANSWERS["shipping_policy"] and out["source"] == "kb/shipping.md"


def test_contextual_reasoning_failure_misreads_the_selected_document() -> None:
    warranty = ask("Does the warranty cover accidental drops and liquid spills?", BASELINE, "reasoning_failure_contextual")
    assert warranty["source"] == "kb/warranty.md" and "covers accidental damage" in warranty["answer"]
    express = ask("How much does express shipping cost?", MULTI, "reasoning_failure_contextual")["answer"]
    assert express == "Express shipping takes 3 to 5 business days."  # standard-delivery clause misapplied


def test_existing_strategies_are_unchanged() -> None:
    q = "Can I return a defective hardware unit after 45 days of delivery?"
    assert [d["source"] for d in retrieve_documents(q, k=2, strategy="keyword")] == ["kb/warranty.md", "kb/returns.md"]
    assert "sources" not in ask(q, BASELINE)
