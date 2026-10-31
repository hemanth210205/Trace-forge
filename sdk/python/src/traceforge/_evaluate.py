"""Execute Traceforge evaluation runs with your own agent code.

    run = traceforge.create_evaluation_run("Support QA", name="nightly",
                                           evaluators=[{"type": "normalized_match", "field": "answer"}])
    summary = traceforge.evaluate(run["id"], lambda case_input: {"answer": my_agent(case_input["question"])})

Each case runs inside its own trace (tagged ``evaluation``), so failed cases link to the exact execution in
the Trace Explorer. Expected outputs never reach the executor. Unlike telemetry, evaluation API errors are
raised (``EvaluationError``): running an evaluation is an explicit operation whose failure must be visible.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from traceforge._version import __version__


class EvaluationError(RuntimeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{message} (HTTP {status}, {code})")
        self.status = status
        self.code = code


def _call(client: Any, method: str, path: str, body: Any = None, timeout: float = 30.0) -> Any:
    if not client.api_key:
        raise EvaluationError(0, "no_api_key", "An API key is required to run evaluations")
    request = urllib.request.Request(
        client.endpoint + "/api/v1" + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Authorization": f"Bearer {client.api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"traceforge-python/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get("error", {})
        except Exception:
            error = {}
        raise EvaluationError(exc.code, error.get("code", "http_error"), error.get("message", "Request failed")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EvaluationError(0, "network_error", f"Cannot reach Traceforge at {client.endpoint}: {exc}") from None


def create_evaluation_run(
    client: Any, dataset: str, *, name: str, evaluators: list[dict[str, Any]], version: int | None = None
) -> dict[str, Any]:
    """Creates a pending run over a published dataset version (the latest unless ``version`` is given)."""
    body: dict[str, Any] = {"dataset": dataset, "name": name, "evaluators": evaluators}
    if version is not None:
        body["version"] = version
    return _call(client, "POST", "/sdk/eval-runs", body)


def evaluate(
    client: Any,
    run_id: str,
    task: Callable[[Any], Any],
    *,
    trace_name: str | None = None,
    trace_tags: list[str] | None = None,
    trace_metadata: dict[str, Any] | None = None,
    batch_size: int = 25,
    flush_timeout: float = 30.0,
) -> dict[str, Any]:
    """Runs ``task(case_input)`` for every case of the run (sync or async task), submits outputs with their
    trace ids, then finishes the run so the server scores it. Returns the finished run (status, summary,
    pass_rate). A case whose task raises is submitted as an execution error; the run continues."""
    run = _call(client, "GET", f"/sdk/eval-runs/{run_id}")
    name = trace_name or f"eval: {run['dataset_name']} v{run['dataset_version']}"
    pending: list[dict[str, Any]] = []

    def submit(force: bool = False) -> None:
        if pending and (force or len(pending) >= batch_size):
            _call(client, "POST", f"/sdk/eval-runs/{run_id}/results", {"results": list(pending)})
            pending.clear()

    def record(case: dict[str, Any], trace: Any, started: float, output: Any, error: BaseException | None) -> None:
        pending.append({
            "case_id": case["case_id"],
            "output": None if error else client.sanitizer.bounded(output),
            "error": None if error is None else {
                "type": type(error).__name__[:200], "message": client.sanitizer.redact_text(str(error))[:4000]},
            "trace_id": str(trace.id),
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        })
        submit()

    def open_trace(case: dict[str, Any]) -> Any:
        return client.trace(name, input=case["input"], tags=["evaluation", *(trace_tags or [])],
                            metadata={**(trace_metadata or {}), "evaluation_run_id": run_id, "case_key": case["key"]})

    if inspect.iscoroutinefunction(task):

        async def run_all() -> None:
            for case in run["cases"]:
                with open_trace(case) as trace:
                    started, output, error = time.perf_counter(), None, None
                    try:
                        output = await task(case["input"])
                        trace.set_output(output)
                    except Exception as exc:
                        error = exc
                        trace.record_exception(exc)
                record(case, trace, started, output, error)

        asyncio.run(run_all())
    else:
        for case in run["cases"]:
            with open_trace(case) as trace:
                started, output, error = time.perf_counter(), None, None
                try:
                    output = task(case["input"])
                    trace.set_output(output)
                except Exception as exc:
                    error = exc
                    trace.record_exception(exc)
            record(case, trace, started, output, error)

    submit(force=True)
    client.flush(flush_timeout)  # traces must reach the server before scoring links them
    return _call(client, "POST", f"/sdk/eval-runs/{run_id}/finish")


def run_experiment(
    client: Any,
    experiment_id: str,
    task: Callable[[Any, dict[str, Any]], Any],
    **options: Any,
) -> dict[str, Any]:
    """Replays the experiment's dataset version once per arm: ``task(case_input, config)`` receives the baseline,
    then the candidate configuration. Each case runs in its own trace tagged with the experiment and arm. Arms
    whose runs are already scored are skipped (so an interrupted experiment can be resumed). Returns the
    completed experiment with its comparison. Traceforge never changes your agent: applying a configuration
    is up to ``task``."""
    experiment = _call(client, "POST", f"/sdk/experiments/{experiment_id}/start")
    for arm in ("baseline", "candidate"):
        spec = experiment[arm]
        if spec["run_status"] not in ("pending", "running"):
            continue
        config = spec["config"]
        if inspect.iscoroutinefunction(task):

            async def arm_task(case_input: Any, _config: dict[str, Any] = config) -> Any:
                return await task(case_input, _config)

        else:

            def arm_task(case_input: Any, _config: dict[str, Any] = config) -> Any:  # type: ignore[misc]
                return task(case_input, _config)

        evaluate(client, spec["run_id"], arm_task, trace_name=f"experiment: {experiment['name']} [{arm}]",
                 trace_tags=["experiment", arm], trace_metadata={"experiment_id": experiment_id, "arm": arm}, **options)
    return _call(client, "POST", f"/sdk/experiments/{experiment_id}/complete")
