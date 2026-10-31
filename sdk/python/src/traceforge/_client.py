"""Client, traces, spans and instrumentation helpers."""

from __future__ import annotations

import atexit
import contextvars
import functools
import inspect
import logging
import math
import os
import time
import traceback
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, TypeVar

from traceforge._serialize import Sanitizer, _safe_repr
from traceforge._transport import BatchSender, HttpTransport, SendResult, Transport

logger = logging.getLogger("traceforge")

SpanKind = Literal["llm", "tool", "retrieval", "agent", "chain", "custom"]
Status = Literal["unset", "ok", "error"]
F = TypeVar("F", bound=Callable[..., Any])

_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar("traceforge_trace", default=None)
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar("traceforge_span", default=None)


# One wall-clock reading per process, advanced with the high-resolution monotonic counter. Reading the wall
# clock per span is too coarse on some platforms (Windows ticks in milliseconds or more), which gave
# consecutive spans identical start times and lost their order. Timestamps stay monotonic and ordered
# within a process; they do not follow later system clock adjustments.
_ANCHOR_WALL = datetime.now(timezone.utc)
_ANCHOR_PERF = time.perf_counter()


def _now() -> datetime:
    return _ANCHOR_WALL + timedelta(seconds=time.perf_counter() - _ANCHOR_PERF)


class _Clock:
    def __init__(self) -> None:
        self.started_at = _now()

    def end(self) -> datetime:
        return max(_now(), self.started_at)


class _Observed:
    """Shared behaviour of traces and spans: guarded setters, status, error capture, context management."""

    _client: Client

    def __init__(self, client: Client, name: str, input: Any, metadata: dict[str, Any] | None) -> None:
        self._client = client
        self.id = uuid.uuid4()
        self.name = (name or "unnamed")[:200]
        self.input = input
        self.output: Any = None
        self.metadata: dict[str, Any] = dict(metadata or {})
        self.status: Status = "unset"
        self.error: dict[str, Any] | None = None
        self._clock = _Clock()
        self.ended_at: datetime | None = None
        self._tokens: list[contextvars.Token[Any]] = []

    @property
    def started_at(self) -> datetime:
        return self._clock.started_at

    @property
    def ended(self) -> bool:
        return self.ended_at is not None

    def set_input(self, value: Any) -> None:
        self.input = value

    def set_output(self, value: Any) -> None:
        self.output = value

    def set_metadata(self, **values: Any) -> None:
        self.metadata.update(values)

    def set_status(self, status: Status) -> None:
        self.status = status

    def record_exception(self, exc: BaseException) -> None:
        """Marks this as failed and keeps the exception type, message and (redacted) traceback."""
        self.status = "error"
        self.error = {
            "type": type(exc).__name__[:200],
            "message": str(exc)[:4000],
            "stacktrace": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-16000:],
        }

    def _finish(self, exc: BaseException | None) -> None:
        if exc is not None and not isinstance(exc, GeneratorExit):
            self.record_exception(exc)
        elif self.status == "unset":
            self.status = "ok"
        self.end()

    # sync and async context managers share the same implementation
    def __enter__(self) -> Any:
        self._activate()
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        try:
            self._finish(exc)
        finally:
            self._deactivate()

    async def __aenter__(self) -> Any:
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> None:
        self.__exit__(exc_type, exc, tb)

    def _activate(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _deactivate(self) -> None:
        while self._tokens:
            token = self._tokens.pop()
            try:
                token.var.reset(token)
            except ValueError:
                # Exited in a different context than it was entered (e.g. another task); leave it alone.
                pass

    def end(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class Span(_Observed):
    def __init__(
        self,
        client: Client,
        trace: Trace,
        name: str,
        kind: SpanKind,
        parent: Span | None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(client, name, input, metadata)
        self.trace = trace
        self.trace_id = trace.id
        self.parent_id = parent.id if parent else None
        self.kind: SpanKind = kind
        self.attributes: dict[str, Any] = dict(attributes or {})
        self.model: str | None = None
        self.prompt_tokens: int | None = None
        self.completion_tokens: int | None = None
        self.total_tokens: int | None = None
        self.cost_usd: float | None = None
        # Set when this span had to create its own trace because none was active.
        self._owns_trace = False

    # --- kind-specific helpers ------------------------------------------------------------------------------

    def set_attributes(self, **values: Any) -> None:
        self.attributes.update(values)

    def set_llm(
        self,
        *,
        model: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        cost_usd: float | None = None,
        **params: Any,
    ) -> None:
        """Model and usage of an LLM call. Extra keyword arguments (temperature, ...) go to attributes."""
        if model is not None:
            self.model = model
        if prompt_tokens is not None:
            self.prompt_tokens = prompt_tokens
        if completion_tokens is not None:
            self.completion_tokens = completion_tokens
        if total_tokens is not None:
            self.total_tokens = total_tokens
        if cost_usd is not None:
            self.cost_usd = cost_usd
        if params:
            self.attributes.setdefault("llm", {}).update(params)

    def set_tool(self, name: str | None = None, arguments: Any = None) -> None:
        tool: dict[str, Any] = self.attributes.setdefault("tool", {})
        if name is not None:
            tool["name"] = name
        if arguments is not None:
            tool["arguments"] = arguments

    def add_documents(self, documents: Iterable[dict[str, Any]]) -> None:
        """Retrieved documents. Each should have ``id`` and/or ``source``; ``score`` and ``content`` optional."""
        self.attributes.setdefault("documents", []).extend(dict(d) for d in documents)

    # --- lifecycle ------------------------------------------------------------------------------------------

    def _activate(self) -> None:
        self._tokens.append(_current_span.set(self))
        if self._owns_trace:
            self._tokens.append(_current_trace.set(self.trace))

    def end(self) -> None:
        if self.ended:
            return
        self.ended_at = self._clock.end()
        self._client._emit_span(self)
        if self._owns_trace:
            self.trace.status = self.status if self.trace.status == "unset" else self.trace.status
            self.trace.output = self.output if self.trace.output is None else self.trace.output
            self.trace.end()


class Trace(_Observed):
    def __init__(
        self,
        client: Client,
        name: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        tags: Iterable[str] = (),
        user_id: str | None = None,
        session_id: str | None = None,
        trace_id: uuid.UUID | str | None = None,
    ) -> None:
        super().__init__(client, name, input, metadata)
        if trace_id is not None:
            self.id = uuid.UUID(str(trace_id))
        self.tags = [str(t)[:64] for t in tags if str(t)][:20]
        self.user_id = user_id
        self.session_id = session_id

    def set_tags(self, *tags: str) -> None:
        self.tags = [str(t)[:64] for t in tags if str(t)][:20]

    def start_span(
        self,
        name: str,
        kind: SpanKind = "custom",
        *,
        parent: Span | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Manual API: a span in this trace. It is not made current; call ``end()`` (or use ``with``)."""
        return Span(self._client, self, name, kind, parent, input, metadata, attributes)

    def _activate(self) -> None:
        self._tokens.append(_current_trace.set(self))
        self._tokens.append(_current_span.set(None))

    def end(self) -> None:
        if self.ended:
            return
        self.ended_at = self._clock.end()
        self._client._emit_trace(self)


class _NullTransport:
    def send(self, payload: dict[str, Any]) -> SendResult:
        return SendResult(202)


class Client:
    """Holds configuration and the background sender. Create one with :func:`traceforge.init`."""

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        *,
        enabled: bool = True,
        capture_inputs: bool = True,
        capture_outputs: bool = True,
        redact_keys: Iterable[str] = (),
        redact_patterns: Iterable[str] = (),
        max_field_bytes: int = 32 * 1024,
        max_queue_size: int = 10_000,
        max_batch_items: int = 100,
        max_batch_bytes: int = 1_000_000,
        flush_interval: float = 1.0,
        timeout: float = 5.0,
        max_retries: int = 3,
        raise_on_error: bool = False,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key or os.environ.get("TRACEFORGE_API_KEY")
        self.endpoint = (endpoint or os.environ.get("TRACEFORGE_ENDPOINT") or "http://127.0.0.1:8000").rstrip("/")
        self.capture_inputs = capture_inputs
        self.capture_outputs = capture_outputs
        self.raise_on_error = raise_on_error
        self.sanitizer = Sanitizer(redact_keys, redact_patterns, max_field_bytes=max_field_bytes)
        if transport is None and enabled and not self.api_key:
            logger.warning("traceforge: no API key (pass api_key or set TRACEFORGE_API_KEY); telemetry is disabled")
            enabled = False
        self.enabled = enabled
        if transport is None:
            transport = HttpTransport(self.endpoint, self.api_key or "", timeout) if enabled else _NullTransport()
        self._sender = BatchSender(
            transport,
            max_queue_size=max_queue_size,
            max_batch_items=max_batch_items,
            max_batch_bytes=max_batch_bytes,
            flush_interval=flush_interval,
            max_retries=max_retries,
            sleep=sleep,
        )
        atexit.register(self.shutdown)

    # --- public API -------------------------------------------------------------------------------------------

    def trace(self, name: str, **kwargs: Any) -> Trace:
        """A new trace; use as a (sync or async) context manager to make it current."""
        return Trace(self, name, **kwargs)

    def span(
        self,
        name: str,
        kind: SpanKind = "custom",
        *,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """A span under the current span/trace. Without an active trace it starts one of the same name."""
        trace = _current_trace.get()
        owns = trace is None or trace.ended
        if owns:
            trace = Trace(self, name, input=input)
        assert trace is not None
        parent = None if owns else _current_span.get()
        if parent is not None and parent.trace is not trace:
            parent = None
        span = Span(self, trace, name, kind, parent, input, metadata, attributes)
        span._owns_trace = owns
        return span

    def observe(
        self,
        name: str | None = None,
        kind: SpanKind = "custom",
        *,
        capture_input: bool = True,
        capture_output: bool = True,
    ) -> Callable[[F], F]:
        """Decorator: runs the function inside a span, capturing arguments, return value and exceptions."""

        def decorate(func: F) -> F:
            span_name = name or func.__qualname__
            signature = _signature(func)

            def start(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Span:
                captured = _bind(signature, args, kwargs) if capture_input else None
                return self.span(span_name, kind, input=captured)

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    span = start(args, kwargs)
                    async with span:
                        result = await func(*args, **kwargs)
                        if capture_output:
                            span.set_output(result)
                        return result

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                span = start(args, kwargs)
                with span:
                    result = func(*args, **kwargs)
                    if capture_output:
                        span.set_output(result)
                    return result

            return wrapper  # type: ignore[return-value]

        return decorate

    def flush(self, timeout: float = 10.0) -> bool:
        return self._sender.flush(timeout)

    def shutdown(self, timeout: float = 10.0) -> bool:
        try:
            atexit.unregister(self.shutdown)
        except Exception:
            pass
        return self._sender.shutdown(timeout)

    def stats(self) -> dict[str, Any]:
        return self._sender.stats.snapshot()

    # --- emission -----------------------------------------------------------------------------------------

    def _guard(self, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            if self.raise_on_error:
                raise
            logger.warning("traceforge: failed to record telemetry", exc_info=True)

    def _short_text(self, value: Any, limit: int = 200) -> str | None:
        if value is None or value == "":
            return None
        text = value if isinstance(value, str) else _safe_repr(value)
        return self.sanitizer.redact_text(text)[:limit] or None

    def _common(self, item: _Observed) -> dict[str, Any]:
        clean = self.sanitizer
        error = None
        if item.error:
            error = {k: clean.redact_text(v) if isinstance(v, str) else v for k, v in item.error.items()}
        return {
            "id": str(item.id),
            "name": clean.redact_text(item.name),
            "status": item.status,
            "started_at": item.started_at.isoformat(),
            "ended_at": item.ended_at.isoformat() if item.ended_at else None,
            "input": clean.bounded(item.input) if self.capture_inputs else None,
            "output": clean.bounded(item.output) if self.capture_outputs else None,
            "metadata": clean.bounded(item.metadata, clean.max_metadata_bytes) if item.metadata else {},
            **({"error": error} if isinstance(item, Span) else {}),
        }

    def _emit_trace(self, trace: Trace) -> None:
        def build() -> None:
            record = self._common(trace)
            metadata = record["metadata"]
            record.update(
                metadata=metadata if isinstance(metadata, dict) and not metadata.get("_truncated") else {"_truncated": True},
                tags=trace.tags,
                user_id=self._short_text(trace.user_id),
                session_id=self._short_text(trace.session_id),
            )
            self._sender.enqueue("trace", record)

        self._guard(build)

    def _emit_span(self, span: Span) -> None:
        def build() -> None:
            record = self._common(span)
            metadata = record["metadata"]
            attributes = self.sanitizer.bounded(span.attributes) if span.attributes else {}
            record.update(
                metadata=metadata if isinstance(metadata, dict) and not metadata.get("_truncated") else {"_truncated": True},
                trace_id=str(span.trace_id),
                parent_span_id=str(span.parent_id) if span.parent_id else None,
                kind=span.kind,
                attributes=attributes if isinstance(attributes, dict) else {"value": attributes},
                # Each usage field is coerced on its own: one malformed value must not drop the whole span.
                model=self._short_text(span.model),
                prompt_tokens=_non_negative_int(span.prompt_tokens),
                completion_tokens=_non_negative_int(span.completion_tokens),
                total_tokens=_non_negative_int(span.total_tokens),
                cost_usd=_cost(span.cost_usd),
            )
            self._sender.enqueue("span", record)

        self._guard(build)


def _non_negative_int(value: Any) -> int | None:
    try:
        return None if value is None or isinstance(value, bool) else max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _cost(value: Any) -> str | None:
    try:
        cost = None if value is None or isinstance(value, bool) else float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return f"{max(0.0, cost):.6f}" if cost is not None and math.isfinite(cost) else None


def _signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def _bind(signature: inspect.Signature | None, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    if signature is None:
        return {"args": list(args), "kwargs": kwargs}
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return {"args": list(args), "kwargs": kwargs}
    return {k: v for k, v in bound.arguments.items() if k not in ("self", "cls")}


def current_span() -> Span | None:
    return _current_span.get()


def current_trace() -> Trace | None:
    return _current_trace.get()
