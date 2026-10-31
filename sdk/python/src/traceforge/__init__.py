"""Traceforge Python SDK.

    import traceforge

    traceforge.init(api_key="tf_...", endpoint="http://127.0.0.1:8000")

    with traceforge.trace("support-request", input={"question": q}) as t:
        with traceforge.span("kb-search", kind="retrieval", input=q) as s:
            s.add_documents([{"id": "doc-7", "source": "kb/shipping.md", "score": 0.91}])
        answer = answer_question(q)          # an @traceforge.observe(kind="llm") function
        t.set_output(answer)

    traceforge.shutdown()                    # flush before exit (also registered with atexit)

Module-level functions use the client created by :func:`init`. Before ``init`` they are safe no-ops.
"""

from collections.abc import Callable
from typing import Any

from traceforge import _evaluate
from traceforge._client import Client, Span, SpanKind, Trace, current_span, current_trace
from traceforge._evaluate import EvaluationError
from traceforge._transport import SendResult, Transport
from traceforge._version import __version__

__all__ = [
    "Client", "EvaluationError", "SendResult", "Span", "SpanKind", "Trace", "Transport", "__version__", "current_span",
    "create_evaluation_run", "current_trace", "evaluate", "run_experiment", "flush", "get_client", "init", "observe", "shutdown",
    "span", "trace",
]

_client: Client | None = None


def init(api_key: str | None = None, endpoint: str | None = None, **options: Any) -> Client:
    """Creates the global client (replacing, and flushing, any previous one). See :class:`Client`."""
    global _client
    if _client is not None:
        _client.shutdown()
    _client = Client(api_key, endpoint, **options)
    return _client


def get_client() -> Client:
    """The global client; a disabled no-op client if :func:`init` was not called."""
    global _client
    if _client is None:
        _client = Client(enabled=False)
    return _client


def trace(name: str, **kwargs: Any) -> Trace:
    return get_client().trace(name, **kwargs)


def span(name: str, kind: SpanKind = "custom", **kwargs: Any) -> Span:
    return get_client().span(name, kind, **kwargs)


def observe(name: str | None = None, kind: SpanKind = "custom", **kwargs: Any) -> Callable[[Any], Any]:
    """Decorator for sync and async functions. The client is resolved when the function is *called*,
    so decorating at import time before :func:`init` works."""

    def decorate(func: Any) -> Any:
        import functools
        import inspect

        cache: dict[int, Any] = {}

        def resolve() -> Any:
            client = get_client()
            wrapped = cache.get(id(client))
            if wrapped is None:
                cache.clear()
                wrapped = cache[id(client)] = client.observe(name or func.__qualname__, kind, **kwargs)(func)
            return wrapped

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_proxy(*args: Any, **kw: Any) -> Any:
                return await resolve()(*args, **kw)

            return async_proxy

        @functools.wraps(func)
        def proxy(*args: Any, **kw: Any) -> Any:
            return resolve()(*args, **kw)

        return proxy

    return decorate


def flush(timeout: float = 10.0) -> bool:
    return get_client().flush(timeout)


def shutdown(timeout: float = 10.0) -> bool:
    return get_client().shutdown(timeout)


def create_evaluation_run(dataset: str, *, name: str, evaluators: list[dict[str, Any]], version: int | None = None) -> dict[str, Any]:
    """Creates a pending evaluation run over a published dataset version (latest by default)."""
    return _evaluate.create_evaluation_run(get_client(), dataset, name=name, evaluators=evaluators, version=version)


def evaluate(run_id: str, task: Callable[[Any], Any], **options: Any) -> dict[str, Any]:
    """Executes an evaluation run with ``task(case_input)``; see :mod:`traceforge._evaluate`."""
    return _evaluate.evaluate(get_client(), run_id, task, **options)


def run_experiment(experiment_id: str, task: Callable[[Any, dict[str, Any]], Any], **options: Any) -> dict[str, Any]:
    """Executes a Traceforge experiment: ``task(case_input, config)`` runs every case with the baseline and the
    candidate configuration; the server then compares the arms. See :mod:`traceforge._evaluate`."""
    return _evaluate.run_experiment(get_client(), experiment_id, task, **options)
