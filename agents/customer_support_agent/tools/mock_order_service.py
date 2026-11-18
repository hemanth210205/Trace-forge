"""Local mock HTTP server for testing real tool network failure handling."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MOCK_ORDERS: dict[str, dict[str, Any]] = {
    "ORD1001": {
        "order_id": "ORD1001",
        "status": "Shipped",
        "estimated_delivery": "2026-11-20",
    },
    "ORD-202": {
        "order_id": "ORD-202",
        "status": "in_transit",
        "estimated_delivery": "2026-11-20",
        "carrier": "UPS",
        "destination": "Austin, TX",
    },
}


class MockOrderHandler(BaseHTTPRequestHandler):
    retry_counters: dict[str, int] = {}

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard HTTP server request logging."""
        return

    def do_GET(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        parts = path.split("/")
        order_id = parts[-1] if parts else ""

        if order_id == "ORD-TIMEOUT":
            # Simulate upstream gateway timeout
            time.sleep(2.5)
            self.send_response(504)
            self.end_headers()
            return

        if order_id == "ORD-500":
            # Always fail with 500 Internal Server Error
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Internal database cluster connection failure"}')
            return

        if order_id == "ORD-RETRY-SUCCESS":
            # Fails on first attempt, succeeds on second attempt
            count = MockOrderHandler.retry_counters.get(order_id, 0) + 1
            MockOrderHandler.retry_counters[order_id] = count
            if count == 1:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Service temporarily unavailable"}')
                return
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"order_id": "ORD-RETRY-SUCCESS", "status": "Shipped", "estimated_delivery": "2026-11-20"}
                    ).encode()
                )
                return

        if order_id == "ORD-MALFORMED":
            # Return 200 with non-JSON HTML error body
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>502 Bad Gateway: Proxy Error</body></html>")
            return

        if order_id in MOCK_ORDERS:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(MOCK_ORDERS[order_id]).encode())
            return

        # Not found
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": f"Order {order_id} not found in upstream store"}).encode())


class MockOrderServer:
    def __init__(self, port: int = 0) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", port), MockOrderHandler)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
