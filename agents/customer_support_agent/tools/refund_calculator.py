"""Refund calculation tool for Customer Support Agent.

Calculates refund amounts deterministically based on return eligibility, order value, and deductions.
"""

from __future__ import annotations

from typing import Any
import traceforge


@traceforge.observe(name="refund-calculator-tool", kind="tool")
def refund_calculator_tool(
    order_amount: float | int,
    return_eligibility: bool,
    applicable_deductions: float | int = 0.0,
    trigger_failure: bool = False,
) -> dict[str, Any]:
    """Calculates final refund amount and produces an audit breakdown."""
    span = traceforge.current_span()
    if span is not None:
        span.set_tool(
            "refund-calculator-tool",
            {
                "order_amount": order_amount,
                "return_eligibility": return_eligibility,
                "applicable_deductions": applicable_deductions,
            },
        )

    if trigger_failure:
        raise ValueError("Calculation engine arithmetic overflow error during refund ledger computation")

    try:
        amount = float(order_amount)
        deductions = float(applicable_deductions)
    except (TypeError, ValueError) as err:
        raise ValueError(f"Invalid monetary value: order amount and deductions must be numeric: {err}") from err

    if amount < 0:
        raise ValueError(f"Order amount cannot be negative: {amount}")

    if deductions < 0:
        raise ValueError(f"Applicable deductions cannot be negative: {deductions}")

    if not return_eligibility:
        result = {
            "refund_amount": 0.0,
            "currency": "USD",
            "eligibility_status": "ineligible",
            "calculation_breakdown": {
                "order_amount": round(amount, 2),
                "applicable_deductions": 0.0,
                "reason": "Item is not eligible for return under current policy guidelines.",
            },
        }
    else:
        net_refund = max(0.0, round(amount - deductions, 2))
        result = {
            "refund_amount": net_refund,
            "currency": "USD",
            "eligibility_status": "eligible",
            "calculation_breakdown": {
                "order_amount": round(amount, 2),
                "applicable_deductions": round(deductions, 2),
                "net_refund": net_refund,
                "reason": "Item approved for return refund." if deductions == 0 else f"Approved return with ${deductions:.2f} deduction.",
            },
        }

    if span is not None:
        span.set_output(result)

    return result
