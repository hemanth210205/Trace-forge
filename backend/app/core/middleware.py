"""Pure-ASGI middleware: request IDs, access logging, and request body size limits."""

import json
import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import AppError, error_body
from app.core.logging import request_id_var

access_logger = logging.getLogger("traceforge.access")
error_logger = logging.getLogger("traceforge.errors")
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9\-_.]{1,64}$")
# On every response, errors included: no MIME sniffing of JSON/exports, no framing, no referrer leakage, and no
# caching of authenticated data. Set only where the app did not already choose a value.
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
)


class RequestContextMiddleware:
    """Assigns an X-Request-ID and logs method, path (no query string) and status."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in present)
                headers.append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Rejects bodies over ``max_bytes`` with 413, using Content-Length or a streaming count."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = False
            if too_large:
                await self._reject(send)
                return

        received = 0
        exceeded = False
        started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise AppError(413, "payload_too_large", f"Request body exceeds {self.max_bytes} bytes.")
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            # Whatever the app makes of a truncated body (FastAPI turns a body-read error into a 400) is dropped:
            # the client gets the 413 below.
            if exceeded:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await self._reject(send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(error_body("payload_too_large", f"Request body exceeds {self.max_bytes} bytes.")).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class UnhandledErrorMiddleware:
    """Sends the 500 error envelope for an unhandled exception from *inside* the request-context and CORS layers.

    Starlette's own fallback runs in its outermost middleware, after the request id is gone and outside CORS, so
    that 500 had no ``request_id``, no ``X-Request-ID`` and no CORS headers (the console could not even read it).
    The exception is re-raised afterwards so the server still logs it and test clients still surface it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if not started:
                error_logger.error("unhandled error: %s", type(exc).__name__, exc_info=exc)
                exc._traceforge_logged = True  # type: ignore[attr-defined]  # not logged again by the fallback
                body = json.dumps(error_body("internal_error", "An internal error occurred.")).encode()
                await send({"type": "http.response.start", "status": 500,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
            raise
