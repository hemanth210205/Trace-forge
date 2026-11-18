"""Unit tests and regression tests for Customer Support Agent."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.customer_support_agent.agent import (
    classify_query,
    execute_agent_task,
    load_knowledge_base,
    retrieve_documents,
    select_context,
)
from agents.customer_support_agent.config import KB_DIR


class TestAgentComponents:
    def test_knowledge_base_loading(self) -> None:
        docs = load_knowledge_base(KB_DIR)
        assert len(docs) >= 3
        sources = {d["source"] for d in docs}
        assert "kb/returns.md" in sources
        assert "kb/shipping.md" in sources
        assert "kb/warranty.md" in sources

    def test_query_classification(self) -> None:
        c1 = classify_query("Where is my package ORD-101?")
        assert c1["intent"] == "order_inquiry"
        assert c1["entity"] == "ORD-101"

        c2 = classify_query("Calculate refund deduction for my order")
        assert c2["intent"] == "refund_calculation"

        c3 = classify_query("Does the warranty cover broken screens?")
        assert c3["intent"] == "warranty_policy"

        c4 = classify_query("How many business days does standard shipping take?")
        assert c4["intent"] == "shipping_policy"

    def test_retrieval_keyword(self) -> None:
        docs = retrieve_documents("warranty manufacturing defects", k=2)
        assert len(docs) > 0
        assert docs[0]["source"] == "kb/warranty.md"

    def test_retrieval_empty_override(self) -> None:
        docs = retrieve_documents("warranty", force_empty=True)
        assert len(docs) == 0

    def test_retrieval_irrelevant_override(self) -> None:
        docs = retrieve_documents("warranty", force_irrelevant=True)
        assert len(docs) > 0
        for d in docs:
            assert "returns" not in d["source"]
            assert "shipping" not in d["source"]

    def test_context_selection(self) -> None:
        docs = retrieve_documents("shipping days", k=2)
        selected = select_context(docs, "shipping days")
        assert selected is not None
        assert "shipping" in selected["source"]

    def test_context_selection_force_wrong(self) -> None:
        docs = retrieve_documents("warranty terms", k=2)
        selected = select_context(docs, "warranty terms", force_wrong=True)
        assert selected is not None
        assert "warranty" not in selected["source"]


class TestFailureScenariosAndRegressions:
    def test_normal_successful_flow(self) -> None:
        res = execute_agent_task({"question": "What does the warranty cover?"})
        assert "answer" in res
        assert "warranty covers manufacturing defects" in res["answer"].lower()
        assert res["source"] == "kb/warranty.md"

    def test_case_04_regression_context_selection_failure_fails_answer_match(self) -> None:
        """Regression test for problem 1: case-04 must NOT pass answer match when wrong context is selected."""
        res = execute_agent_task(
            {
                "question": "What items are covered under the standard warranty terms?",
                "override_mode": "context_selection_failure",
            }
        )
        assert "answer" in res
        # Must NOT produce the correct warranty answer
        assert "the warranty covers manufacturing defects" not in res["answer"].lower()
        # Source must be the wrong selected document, not warranty.md
        assert res.get("source") != "kb/warranty.md"
        assert "shipping" in res.get("source", "")
        # Answer must be drawn from the selected shipping context
        assert "shipping policy" in res["answer"].lower()

    def test_case_12_regression_insufficient_evidence_fails_answer_match(self) -> None:
        """Regression test for problem 2: case-12 must report missing details when evidence lacks content."""
        res = execute_agent_task(
            {
                "question": "What happens if I try to return something with missing tags?",
                "override_mode": "insufficient_evidence",
            }
        )
        assert "answer" in res
        # Must report inability to answer due to missing details
        assert "does not contain sufficient details" in res["answer"].lower()
        # Must NOT falsely pass with the full answer
        assert "items must have original packaging and tags" not in res["answer"].lower()

    def test_candidate_intent_aligned_context_selection_fixes_case_04(self) -> None:
        """Verifies candidate context strategy properly fixes context selection."""
        docs = retrieve_documents("What items are covered under the standard warranty terms?", k=2)
        # Using intent_aligned strategy
        selected = select_context(
            docs,
            "What items are covered under the standard warranty terms?",
            strategy="intent_aligned",
        )
        assert selected is not None
        assert "warranty" in selected["source"]

    def test_positive_controls(self) -> None:
        # Case 13 positive order lookup
        res13 = execute_agent_task({"question": "Check delivery for ORD-202"})
        assert "UPS" in res13["answer"] and "Austin, TX" in res13["answer"]

        # Case 14 positive return policy
        res14 = execute_agent_task({"question": "Can I return an item within 30 days?"})
        assert "30 days" in res14["answer"].lower() and res14["source"] == "kb/returns.md"

        # Case 15 positive refund calculator
        res15 = execute_agent_task(
            {"question": "Calculate refund", "order_amount": 100.0, "return_eligibility": True, "deductions": 15.0}
        )
        assert "$85.00" in res15["answer"]

        # Case 16 positive express shipping
        res16 = execute_agent_task({"question": "How much is express shipping?"})
        assert "14.99" in res16["answer"] and res16["source"] == "kb/shipping.md"

    def test_all_50_dataset_cases_execute_cleanly(self) -> None:
        scenarios_file = Path(__file__).resolve().parent.parent / "scenarios" / "test_cases.json"
        with open(scenarios_file, encoding="utf-8") as f:
            cases = json.load(f)

        assert len(cases) == 50
        for case in cases:
            output = execute_agent_task(case["input"])
            assert isinstance(output, dict)
            if case["input"].get("override_mode") == "output_format_failure":
                assert "reply_text" in output
            else:
                assert "answer" in output
