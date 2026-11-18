"""HTTP-capable order lookup tool with retry behavior and failure resilience."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import traceforge


@traceforge.observe(name="http-order-lookup-tool", kind="tool")
def http_order_lookup_tool(
    order_id: str,
    base_url: str,
    timeout: float = 1.0,
    max_retries: int = 2,
    retry_delay: float = 0.1,
) -> dict[str, Any]:
    """Looks up order details from remote HTTP service with automated retry on transient failures."""
    span = traceforge.current_span()
    if span is not None:
        span.set_tool("http-order-lookup-tool", {"order_id": order_id, "base_url": base_url})

    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("Invalid order ID: must be a non-empty string.")

    cleaned_id = order_id.strip().upper()
    url = f"{base_url.rstrip('/')}/api/v1/orders/{cleaned_id}"

    last_error: Exception | None = None
    attempts = 0

    while attempts <= max_retries:
        attempts += 1
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "Traceforge-Agent-Tool/1.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                raw_data = response.read().decode("utf-8")

                if "application/json" not in content_type and not raw_data.strip().startswith("{"):
                    raise ValueError(f"Malformed HTTP response: expected JSON payload but received {content_type}")

                try:
                    data = json.loads(raw_data)
                except json.JSONDecodeError as err:
                    raise ValueError(f"Malformed JSON in response body: {err}") from err

                if span is not None:
                    span.set_output(data)
                return data

        except urllib.error.HTTPError as http_err:
            last_error = http_err
            # 404 indicates non-existent order; do not retry
            if http_err.code == 404:
                raise LookupError(f"Order ID '{cleaned_id}' was not found in remote order store (HTTP 404).") from http_err

            # 5xx indicates transient upstream failure; retry if attempts remain
            if http_err.code in (500, 502, 503, 504) and attempts <= max_retries:
                time.sleep(retry_delay * attempts)
                continue

            raise RuntimeError(f"Remote order service returned unrecoverable HTTP {http_err.code} error.") from http_err

        except (urllib.error.URLError, TimeoutError, OSError) as net_err:
            last_error = net_err
            # Network timeout; retry if attempts remain
            if attempts <= max_retries:
                time.sleep(retry_delay * attempts)
                continue

            raise TimeoutError(f"Remote order service timed out after {attempts} attempts: {net_err}") from net_err

    raise RuntimeError(f"Order lookup failed after {max_retries + 1} attempts: {last_error}")
