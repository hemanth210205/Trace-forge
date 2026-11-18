"""Order lookup tool for Customer Support Agent.

Simulates querying an order database deterministically and instruments execution using Traceforge SDK.
"""

from __future__ import annotations

from typing import Any
import traceforge

ORDERS: dict[str, dict[str, str]] = {
    "ORD1001": {
        "status": "Shipped",
        "estimated_delivery": "2026-11-20",
    },
    "ORD1002": {
        "status": "Processing",
        "estimated_delivery": "2026-11-22",
    },
    "ORD1003": {
        "status": "Delivered",
        "estimated_delivery": "2026-11-15",
    },
    "ORD-101": {
        "status": "delivered",
        "estimated_delivery": "2026-11-15",
        "carrier": "FedEx",
        "destination": "New York, NY",
    },
    "ORD-202": {
        "status": "in_transit",
        "estimated_delivery": "2026-11-20",
        "carrier": "UPS",
        "destination": "Austin, TX",
    },
    "ORD-303": {
        "status": "processing",
        "estimated_delivery": "2026-11-25",
        "carrier": "USPS",
        "destination": "Seattle, WA",
    },
}


@traceforge.observe(name="order-lookup-tool", kind="tool")
def order_lookup_tool(order_id: str, trigger_failure: bool = False) -> dict[str, Any]:
    """Looks up order status and delivery estimates by order ID."""
    span = traceforge.current_span()
    if span is not None:
        span.set_tool("order-lookup-tool", {"order_id": order_id})

    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("Invalid order ID: must be a non-empty string.")

    cleaned_id = order_id.strip().upper()

    if trigger_failure:
        raise RuntimeError(f"Database connection timeout while querying order store for {cleaned_id}")

    if cleaned_id not in ORDERS:
        raise LookupError(f"Order ID '{cleaned_id}' was not found in customer order records.")

    order_info = ORDERS[cleaned_id]
    result: dict[str, Any] = {
        "order_id": cleaned_id,
        "status": order_info["status"],
        "estimated_delivery": order_info["estimated_delivery"],
    }
    if "carrier" in order_info:
        result["carrier"] = order_info["carrier"]
    if "destination" in order_info:
        result["destination"] = order_info["destination"]

    if span is not None:
        span.set_output(result)

    return result
