"""Unit tests for Customer Support Agent tools."""

from __future__ import annotations

import pytest

from agents.customer_support_agent.tools.order_lookup import order_lookup_tool
from agents.customer_support_agent.tools.refund_calculator import refund_calculator_tool


class TestOrderLookupTool:
    def test_lookup_successful_known_order(self) -> None:
        result = order_lookup_tool("ORD1001")
        assert result["order_id"] == "ORD1001"
        assert result["status"] == "Shipped"
        assert "estimated_delivery" in result

    def test_lookup_hyphenated_order(self) -> None:
        result = order_lookup_tool("ORD-202")
        assert result["order_id"] == "ORD-202"
        assert result["status"] == "in_transit"
        assert result.get("carrier") == "UPS"

    def test_lookup_unknown_order_raises_lookup_error(self) -> None:
        with pytest.raises(LookupError, match="was not found"):
            order_lookup_tool("ORD-99999")

    def test_lookup_empty_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Invalid order ID"):
            order_lookup_tool("   ")

    def test_lookup_failure_injection(self) -> None:
        with pytest.raises(RuntimeError, match="Database connection timeout"):
            order_lookup_tool("ORD1001", trigger_failure=True)


class TestRefundCalculatorTool:
    def test_refund_eligible_no_deductions(self) -> None:
        result = refund_calculator_tool(order_amount=120.0, return_eligibility=True)
        assert result["refund_amount"] == 120.0
        assert result["eligibility_status"] == "eligible"
        assert result["calculation_breakdown"]["applicable_deductions"] == 0.0

    def test_refund_eligible_with_deductions(self) -> None:
        result = refund_calculator_tool(order_amount=100.0, return_eligibility=True, applicable_deductions=15.0)
        assert result["refund_amount"] == 85.0
        assert result["eligibility_status"] == "eligible"

    def test_refund_ineligible(self) -> None:
        result = refund_calculator_tool(order_amount=80.0, return_eligibility=False)
        assert result["refund_amount"] == 0.0
        assert result["eligibility_status"] == "ineligible"

    def test_refund_invalid_negative_amount(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            refund_calculator_tool(order_amount=-10.0, return_eligibility=True)

    def test_refund_failure_injection(self) -> None:
        with pytest.raises(ValueError, match="Calculation engine arithmetic overflow"):
            refund_calculator_tool(order_amount=50.0, return_eligibility=True, trigger_failure=True)
