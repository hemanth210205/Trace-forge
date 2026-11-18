"""Integration tests for real HTTP tool failure modes and retry behavior."""

from __future__ import annotations

import pytest

from agents.customer_support_agent.tools.http_order_lookup import http_order_lookup_tool
from agents.customer_support_agent.tools.mock_order_service import MockOrderServer


@pytest.fixture(scope="module")
def mock_server():
    server = MockOrderServer()
    server.start()
    yield f"http://127.0.0.1:{server.port}"
    server.stop()


class TestHttpToolIntegration:
    def test_successful_http_lookup(self, mock_server: str) -> None:
        result = http_order_lookup_tool("ORD1001", base_url=mock_server)
        assert result["order_id"] == "ORD1001"
        assert result["status"] == "Shipped"
        assert result["estimated_delivery"] == "2026-11-20"

    def test_hyphenated_order_lookup(self, mock_server: str) -> None:
        result = http_order_lookup_tool("ORD-202", base_url=mock_server)
        assert result["order_id"] == "ORD-202"
        assert result["status"] == "in_transit"
        assert result["carrier"] == "UPS"

    def test_http_404_missing_order_raises_lookup_error(self, mock_server: str) -> None:
        with pytest.raises(LookupError, match="not found in remote order store"):
            http_order_lookup_tool("ORD-NONEXISTENT", base_url=mock_server)

    def test_http_timeout_handling(self, mock_server: str) -> None:
        # Use low timeout to trigger timeout error
        with pytest.raises(TimeoutError, match="timed out"):
            http_order_lookup_tool("ORD-TIMEOUT", base_url=mock_server, timeout=0.3, max_retries=1, retry_delay=0.05)

    def test_http_500_server_error_and_retry_exhaustion(self, mock_server: str) -> None:
        with pytest.raises(RuntimeError, match="HTTP 500"):
            http_order_lookup_tool("ORD-500", base_url=mock_server, max_retries=2, retry_delay=0.05)

    def test_http_transient_error_successful_retry(self, mock_server: str) -> None:
        # Initial request encounters 503, retries and succeeds with 200
        result = http_order_lookup_tool("ORD-RETRY-SUCCESS", base_url=mock_server, max_retries=2, retry_delay=0.05)
        assert result["order_id"] == "ORD-RETRY-SUCCESS"
        assert result["status"] == "Shipped"

    def test_malformed_html_response_handling(self, mock_server: str) -> None:
        with pytest.raises(ValueError, match="Malformed HTTP response"):
            http_order_lookup_tool("ORD-MALFORMED", base_url=mock_server)
