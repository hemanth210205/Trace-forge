"""HTTP transport (stdlib only) and the bounded background sender."""

import json
import logging
import queue
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from traceforge._version import __version__

logger = logging.getLogger("traceforge")

INGEST_PATH = "/api/v1/ingest/batch"
# Server-side limits (backend/app/schemas/trace.py); batches are split to stay under them.
SERVER_MAX_TRACES = 100
SERVER_MAX_SPANS = 1000


@dataclass(frozen=True)
class SendResult:
    status: int  # HTTP status, or 0 for a network error
    retry_after: float | None = None
    detail: str = ""


class Transport(Protocol):
    def send(self, payload: dict[str, Any]) -> SendResult: ...


class HttpTransport:
    def __init__(self, endpoint: str, api_key: str, timeout: float) -> None:
        self.url = endpoint.rstrip("/") + INGEST_PATH
        self.api_key = api_key
        self.timeout = timeout

    def send(self, payload: dict[str, Any]) -> SendResult:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": f"traceforge-python/{__version__}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return SendResult(response.status)
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                detail = exc.read(2000).decode(errors="replace")
            except Exception:
                detail = ""
            return SendResult(exc.code, _parse_retry_after(retry_after), detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return SendResult(0, None, type(exc).__name__)


def _parse_retry_after(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class Stats:
    queued: int = 0
    sent_items: int = 0
    sent_batches: int = 0
    dropped_items: int = 0  # buffer full, or a batch that could not be delivered
    failed_batches: int = 0
    retries: int = 0
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, **deltas: int) -> None:
        with self._lock:
            for name, delta in deltas.items():
                setattr(self, name, getattr(self, name) + delta)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}


_FLUSH = object()
_STOP = object()


class BatchSender:
    """Owns a bounded queue and one daemon thread that batches and delivers events.

    ``enqueue`` never blocks: when the buffer is full the event is dropped and counted. Delivery retries
    network errors, 5xx and 429 (honouring Retry-After) with exponential backoff; other 4xx responses are
    not retried. Nothing here raises into the caller's code.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        max_queue_size: int,
        max_batch_items: int,
        max_batch_bytes: int,
        flush_interval: float,
        max_retries: int,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.transport = transport
        self.max_batch_items = max_batch_items
        self.max_batch_bytes = max_batch_bytes
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.sleep = sleep
        self.stats = Stats()
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_queue_size)
        self._thread = threading.Thread(target=self._run, name="traceforge-sender", daemon=True)
        self._stopped = False
        self._thread.start()

    # --- producer side -------------------------------------------------------------------------------------

    def enqueue(self, kind: str, record: dict[str, Any]) -> bool:
        if self._stopped:
            self.stats.add(dropped_items=1)
            return False
        try:
            encoded_size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode())
            self._queue.put_nowait((kind, record, encoded_size))
            self.stats.add(queued=1)
            return True
        except queue.Full:
            self.stats.add(dropped_items=1)
            self.stats.last_error = "buffer full"
            return False

    def flush(self, timeout: float) -> bool:
        """Delivers everything queued so far. Returns False if it did not finish within ``timeout``."""
        if self._stopped or not self._thread.is_alive():
            return self._queue.empty()
        done = threading.Event()
        try:
            self._queue.put((_FLUSH, done), timeout=timeout)
        except queue.Full:
            return False
        return done.wait(timeout)

    def shutdown(self, timeout: float) -> bool:
        flushed = self.flush(timeout)
        self._stopped = True
        try:
            self._queue.put_nowait((_STOP, None))
        except queue.Full:
            pass
        self._thread.join(timeout=max(0.1, timeout))
        return flushed

    # --- sender thread ------------------------------------------------------------------------------------

    def _run(self) -> None:
        traces: list[dict[str, Any]] = []
        spans: list[dict[str, Any]] = []
        size = 0
        deadline = time.monotonic() + self.flush_interval
        while True:
            timeout = max(0.0, deadline - time.monotonic())
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None
            if item is None or item[0] in (_FLUSH, _STOP):
                if traces or spans:
                    self._deliver(traces, spans)
                    traces, spans, size = [], [], 0
                deadline = time.monotonic() + self.flush_interval
                if item is not None and item[0] is _FLUSH:
                    item[1].set()
                if item is not None and item[0] is _STOP:
                    return
                continue
            kind, record, record_size = item
            would_overflow = (
                len(traces) + len(spans) >= self.max_batch_items
                or size + record_size > self.max_batch_bytes
                or (kind == "trace" and len(traces) >= SERVER_MAX_TRACES)
                or (kind == "span" and len(spans) >= SERVER_MAX_SPANS)
            )
            if would_overflow and (traces or spans):
                self._deliver(traces, spans)
                traces, spans, size = [], [], 0
            (traces if kind == "trace" else spans).append(record)
            size += record_size

    def _deliver(self, traces: list[dict[str, Any]], spans: list[dict[str, Any]]) -> None:
        payload = {"traces": traces, "spans": spans}
        count = len(traces) + len(spans)
        for attempt in range(self.max_retries + 1):
            try:
                result = self.transport.send(payload)
            except Exception as exc:  # a custom transport must not kill the sender thread
                result = SendResult(0, None, type(exc).__name__)
            if 200 <= result.status < 300:
                self.stats.add(sent_items=count, sent_batches=1)
                return
            retryable = result.status in (0, 408, 429) or result.status >= 500
            self.stats.last_error = f"HTTP {result.status}" if result.status else f"network: {result.detail}"
            if not retryable or attempt == self.max_retries:
                break
            self.stats.add(retries=1)
            delay = min(self.backoff_max, self.backoff_base * (2**attempt)) * (0.5 + random.random() / 2)
            if result.retry_after is not None:
                delay = min(self.backoff_max, max(delay, result.retry_after))
            self.sleep(delay)
        self.stats.add(failed_batches=1, dropped_items=count)
        if result.status in (401, 403):
            logger.warning("traceforge: telemetry rejected (HTTP %s); check the project API key", result.status)
        else:
            logger.warning("traceforge: dropped a batch of %d items (%s)", count, self.stats.last_error)
