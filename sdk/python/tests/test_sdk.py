"""SDK behaviour with a fake transport (the network boundary), plus one real local HTTP round trip."""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

import traceforge
from traceforge import Client, SendResult


class FakeTransport:
    def __init__(self, responses: list[SendResult] | None = None, block: threading.Event | None = None) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.responses = list(responses or [])
        self.block = block
        self.calls = 0

    def send(self, payload: dict[str, Any]) -> SendResult:
        self.calls += 1
        if self.block is not None:
            self.block.wait(5)
        self.payloads.append(json.loads(json.dumps(payload)))  # must be JSON-serializable as sent
        return self.responses.pop(0) if self.responses else SendResult(202)

    @property
    def traces(self) -> list[dict[str, Any]]:
        return [t for p in self.payloads for t in p["traces"]]

    @property
    def spans(self) -> list[dict[str, Any]]:
        return [s for p in self.payloads for s in p["spans"]]


def make(transport: FakeTransport, **options: Any) -> Client:
    sleeps: list[float] = []
    client = Client("tf_test", transport=transport, flush_interval=0.05, sleep=sleeps.append, **options)
    client.sleeps = sleeps  # type: ignore[attr-defined]
    return client


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport) -> Any:
    c = make(transport)
    yield c
    c.shutdown(2)


def test_nested_spans_record_hierarchy_kinds_timing_and_usage(client: Client, transport: FakeTransport) -> None:
    with client.trace("support", input={"q": "hi"}, tags=["t1", ""], user_id="u1") as t:
        with client.span("agent", "agent") as root:
            with client.span("kb", "retrieval", input="hi") as kb:
                kb.add_documents([{"id": "d1", "source": "kb/a.md", "score": 0.9}])
                time.sleep(0.01)
            with client.span("answer", "llm") as llm:
                llm.set_llm(model="m-1", prompt_tokens=10, completion_tokens=5, cost_usd=0.0012, temperature=0.2)
                llm.set_output("hello")
            with client.span("lookup", "tool") as tool:
                tool.set_tool("orders", {"id": 1})
        t.set_output("hello")
    assert client.flush(2)
    (trace,) = transport.traces
    spans = {s["name"]: s for s in transport.spans}
    assert trace["name"] == "support" and trace["status"] == "ok" and trace["tags"] == ["t1"]
    assert trace["input"] == {"q": "hi"} and trace["output"] == "hello" and trace["user_id"] == "u1"
    assert {s["trace_id"] for s in spans.values()} == {trace["id"]}
    assert spans["agent"]["parent_span_id"] is None
    assert spans["kb"]["parent_span_id"] == spans["agent"]["id"] == spans["answer"]["parent_span_id"]
    assert spans["kb"]["attributes"]["documents"][0]["source"] == "kb/a.md"
    assert spans["kb"]["ended_at"] > spans["kb"]["started_at"]
    assert (spans["answer"]["model"], spans["answer"]["prompt_tokens"], spans["answer"]["cost_usd"]) == ("m-1", 10, "0.001200")
    assert spans["answer"]["attributes"]["llm"] == {"temperature": 0.2}
    assert spans["lookup"]["attributes"]["tool"] == {"name": "orders", "arguments": {"id": 1}}
    assert all(s["status"] == "ok" for s in spans.values())


def test_consecutive_spans_have_strictly_ordered_high_resolution_timestamps(client: Client, transport: FakeTransport) -> None:
    with client.trace("t"):
        for i in range(20):
            with client.span(f"s{i:02d}"):
                pass
    client.flush(2)
    spans = sorted(transport.spans, key=lambda s: s["name"])
    starts = [s["started_at"] for s in spans]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)  # distinct and in execution order
    assert all(s["ended_at"] >= s["started_at"] for s in spans)


def test_exceptions_are_recorded_and_re_raised(client: Client, transport: FakeTransport) -> None:
    with pytest.raises(ValueError, match="boom"):
        with client.trace("t"):
            with client.span("failing", "tool"):
                raise ValueError("boom")
    client.flush(2)
    (span,) = transport.spans
    assert span["status"] == "error" and span["error"]["type"] == "ValueError" and span["error"]["message"] == "boom"
    assert "Traceback" in span["error"]["stacktrace"]
    assert transport.traces[0]["status"] == "error"


def test_decorators_capture_arguments_results_and_errors_for_sync_and_async(client: Client, transport: FakeTransport) -> None:
    @client.observe(kind="tool")
    def add(a: int, b: int = 2) -> int:
        return a + b

    @client.observe(name="fetch", kind="retrieval")
    async def fetch(query: str) -> list[str]:
        await asyncio.sleep(0)
        return [query.upper()]

    @client.observe()
    async def explode() -> None:
        raise RuntimeError("async failure")

    async def main() -> None:
        with client.trace("decorated"):
            assert add(1) == 3
            assert await fetch("x") == ["X"]
            with pytest.raises(RuntimeError):
                await explode()

    asyncio.run(main())
    client.flush(2)
    spans = {s["name"]: s for s in transport.spans}
    assert spans[add.__qualname__]["input"] == {"a": 1} and spans[add.__qualname__]["output"] == 3
    assert spans["fetch"]["input"] == {"query": "x"} and spans["fetch"]["output"] == ["X"]
    assert spans[explode.__qualname__]["status"] == "error"


def test_concurrent_async_tasks_keep_separate_parents(client: Client, transport: FakeTransport) -> None:
    async def worker(label: str) -> None:
        with client.span(f"outer-{label}", "chain"):
            await asyncio.sleep(0.01)
            with client.span(f"inner-{label}"):
                await asyncio.sleep(0.01)

    async def main() -> None:
        async with client.trace("parallel"):
            await asyncio.gather(worker("a"), worker("b"))

    asyncio.run(main())
    client.flush(2)
    spans = {s["name"]: s for s in transport.spans}
    for label in "ab":
        assert spans[f"inner-{label}"]["parent_span_id"] == spans[f"outer-{label}"]["id"]
        assert spans[f"outer-{label}"]["parent_span_id"] is None


def test_span_without_active_trace_starts_its_own(client: Client, transport: FakeTransport) -> None:
    with client.span("standalone", "llm") as s:
        s.set_output("done")
        with client.span("child"):
            pass
    client.flush(2)
    (trace,) = transport.traces
    assert trace["name"] == "standalone" and trace["output"] == "done"
    assert {s["trace_id"] for s in transport.spans} == {trace["id"]}
    assert {s["name"]: s["parent_span_id"] for s in transport.spans}["child"] is not None


def test_manual_api(client: Client, transport: FakeTransport) -> None:
    t = client.trace("manual")
    root = t.start_span("root", "agent")
    child = t.start_span("child", "tool", parent=root)
    child.end()
    root.end()
    t.end()
    t.end()  # idempotent
    client.flush(2)
    assert len(transport.traces) == 1
    spans = {s["name"]: s for s in transport.spans}
    assert spans["child"]["parent_span_id"] == spans["root"]["id"]


def test_redaction_of_keys_patterns_and_custom_rules(transport: FakeTransport) -> None:
    client = make(transport, redact_keys=["customer_ssn"], redact_patterns=[r"\b\d{3}-\d{2}-\d{4}\b"])
    key = "tf_0123abcd_" + "s" * 40
    with client.trace("t", input={"password": "hunter2", "api_key": "abc", "Customer-SSN": "x", "max_tokens": 50,
                                  "note": f"use {key} or Bearer abcdefghijk and 123-45-6789"}):
        with client.span("s") as s:
            s.set_attributes(headers={"Authorization": "Bearer xyz123456789"})
            s.set_metadata(token="secret-token")
            raise_and_record(s, f"failed with {key}")
    client.flush(2)
    sent = json.dumps(transport.payloads)
    for secret in ("hunter2", key, "abcdefghijk", "123-45-6789", "xyz123456789", "secret-token"):
        assert secret not in sent
    t_input = transport.traces[0]["input"]
    assert t_input["password"] == t_input["api_key"] == t_input["Customer-SSN"] == "[REDACTED]"
    assert t_input["max_tokens"] == 50  # token *counts* are not secrets
    client.shutdown(2)


def raise_and_record(span: Any, message: str) -> None:
    try:
        raise PermissionError(message)
    except PermissionError as exc:
        span.record_exception(exc)


def test_oversized_and_unserializable_values_are_bounded(transport: FakeTransport) -> None:
    client = make(transport, max_field_bytes=1024)

    class Weird:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    with client.trace("t", input="x" * 5000):
        with client.span("s", input={"obj": Weird(), "set": {1}, "bytes": b"abc", "nested": [[[[1]]]]}):
            pass
    client.flush(2)
    big = transport.traces[0]["input"]
    assert big["_truncated"] is True and big["original_bytes"] > 5000 and len(json.dumps(big)) < 1024
    assert transport.spans[0]["input"]["obj"] == "<unrepresentable Weird>"
    assert transport.spans[0]["input"]["bytes"] == "<3 bytes>"
    client.shutdown(2)


def test_values_the_server_cannot_store_are_made_safe(client: Client, transport: FakeTransport) -> None:
    """NaN/Infinity and NUL used to reach the server, which rejected the whole batch (other traces were lost)."""
    with client.trace("t\x00name", input={"score": float("nan"), "hi": float("inf"), "lo": float("-inf"), "text": "a\x00b", "k\x00": 1}) as t:
        with t.start_span("llm", kind="llm") as span:
            span.set_output([1.5, float("nan")])
    assert client.flush(2)
    for payload in transport.payloads:
        encoded = json.dumps(payload, allow_nan=False)  # strict JSON, as PostgreSQL requires
        assert "\\u0000" not in encoded and "\x00" not in encoded
    trace = transport.traces[0]
    assert trace["name"] == "tname"
    assert trace["input"] == {"score": "NaN", "hi": "Infinity", "lo": "-Infinity", "text": "ab", "k": 1}
    assert transport.spans[0]["output"] == [1.5, "NaN"]


def test_malformed_llm_usage_keeps_the_span(client: Client, transport: FakeTransport) -> None:
    class Unprintable:
        def __repr__(self) -> str:
            raise RuntimeError("boom")

    with client.trace("t") as t:
        with t.start_span("llm", kind="llm") as span:
            span.set_llm(model=Unprintable(), prompt_tokens="many", completion_tokens=7, cost_usd=float("nan"))  # type: ignore[arg-type]
    assert client.flush(2)
    (span_record,) = transport.spans
    assert span_record["model"] == "<unrepresentable Unprintable>"
    assert span_record["prompt_tokens"] is None and span_record["completion_tokens"] == 7 and span_record["cost_usd"] is None


def test_batches_respect_item_and_byte_limits(transport: FakeTransport) -> None:
    client = make(transport, max_batch_items=10, max_batch_bytes=4000)
    with client.trace("t"):
        for i in range(35):
            with client.span(f"s{i}", input="y" * 300):
                pass
    client.flush(2)
    assert len(transport.spans) == 35 and len(transport.traces) == 1
    assert all(len(p["traces"]) + len(p["spans"]) <= 10 for p in transport.payloads)
    assert all(len(json.dumps(p)) <= 4000 + 500 for p in transport.payloads)
    client.shutdown(2)


def test_retries_transient_failures_with_backoff_and_retry_after(transport: FakeTransport) -> None:
    transport.responses = [SendResult(503), SendResult(0, detail="ConnectionRefusedError"), SendResult(429, retry_after=7)]
    client = make(transport, max_retries=3)
    with client.trace("t"):
        pass
    client.flush(2)
    assert transport.calls == 4 and len(transport.traces) == 4  # same batch re-sent; server upserts are idempotent
    assert client.stats()["sent_items"] == 1 and client.stats()["retries"] == 3
    assert client.sleeps[-1] >= 7  # type: ignore[attr-defined]
    client.shutdown(2)


def test_gives_up_after_max_retries_and_does_not_retry_client_errors(transport: FakeTransport) -> None:
    transport.responses = [SendResult(500)] * 3 + [SendResult(401)]
    client = make(transport, max_retries=2)
    with client.trace("a"):
        pass
    client.flush(2)
    with client.trace("b"):
        pass
    client.flush(2)
    stats = client.stats()
    assert transport.calls == 4  # 3 attempts for the first batch, 1 for the rejected second
    assert stats["failed_batches"] == 2 and stats["dropped_items"] == 2 and stats["sent_items"] == 0
    client.shutdown(2)


def test_full_buffer_drops_instead_of_blocking_the_agent() -> None:
    gate = threading.Event()
    transport = FakeTransport(block=gate)
    client = make(transport, max_queue_size=5, max_batch_items=1)
    started = time.monotonic()
    for i in range(50):
        with client.trace(f"t{i}"):
            pass
    assert time.monotonic() - started < 1.0  # never blocked on the stuck transport
    assert client.stats()["dropped_items"] > 0
    gate.set()
    client.shutdown(5)


def test_telemetry_failures_never_crash_the_agent(caplog: pytest.LogCaptureFixture) -> None:
    class ExplodingTransport:
        def send(self, payload: dict[str, Any]) -> SendResult:
            raise ConnectionError("network down")

    client = make(ExplodingTransport(), max_retries=1)  # type: ignore[arg-type]
    with client.trace("t") as t:
        t.set_metadata(bad=object())
    assert client.flush(2)
    assert client.stats()["failed_batches"] == 1
    client.shutdown(2)


def test_without_api_key_the_client_is_a_disabled_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACEFORGE_API_KEY", raising=False)
    client = Client()
    assert client.enabled is False
    with client.trace("t"):
        with client.span("s"):
            pass
    assert client.flush(2)
    client.shutdown(1)


def test_module_level_api_before_and_after_init(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRACEFORGE_API_KEY", raising=False)

    @traceforge.observe(kind="tool")
    def double(x: int) -> int:
        return x * 2

    assert double(2) == 4  # works before init (no-op)
    transport = FakeTransport()
    traceforge.init("tf_test", transport=transport, flush_interval=0.05)
    try:
        with traceforge.trace("global"):
            assert double(3) == 6
        traceforge.flush(2)
        assert [s["name"] for s in transport.spans] == [double.__qualname__]
    finally:
        traceforge.shutdown(2)


class _Handler(BaseHTTPRequestHandler):
    received: list[tuple[dict[str, str], dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Handler.received.append((dict(self.headers), body))
        self.send_response(202)
        self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


def test_http_transport_posts_json_with_bearer_key() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = Client("tf_key", f"http://127.0.0.1:{server.server_port}", flush_interval=0.05)
        with client.trace("over-http"):
            pass
        assert client.flush(5)
        client.shutdown(2)
    finally:
        server.shutdown()
    headers, body = _Handler.received[-1]
    assert headers["Authorization"] == "Bearer tf_key" and headers["Content-Type"] == "application/json"
    assert headers["User-Agent"].startswith("traceforge-python/")
    assert body["traces"][0]["name"] == "over-http" and body["spans"] == []
