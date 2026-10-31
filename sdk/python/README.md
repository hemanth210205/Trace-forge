# Traceforge Python SDK

Instrument an AI agent and send its traces to a Traceforge server. Pure standard library (Python 3.10+).

```bash
pip install -e sdk/python          # from the Traceforge repository
```

```python
import traceforge

traceforge.init(api_key="tf_...", endpoint="http://127.0.0.1:8000")   # or TRACEFORGE_API_KEY / TRACEFORGE_ENDPOINT

@traceforge.observe(kind="tool")
def lookup_order(order_id: str) -> dict: ...

with traceforge.trace("support-request", input={"question": q}, tags=["prod"], user_id="u-42") as t:
    with traceforge.span("kb-search", kind="retrieval", input=q) as s:
        s.add_documents([{"id": "doc-7", "source": "kb/shipping.md", "score": 0.91}])
    order = lookup_order("A1")
    with traceforge.span("answer", kind="llm", input=prompt) as llm:
        reply = call_model(prompt)
        llm.set_llm(model="gpt-4o-mini", prompt_tokens=812, completion_tokens=64, cost_usd=0.00021)
        llm.set_output(reply)
    t.set_output(reply)

traceforge.shutdown()   # flushes; also runs automatically at interpreter exit
```

## What it captures

The SDK records exactly what you instrument; it does not hook into LLM or agent frameworks automatically.

| API | Use |
| --- | --- |
| `traceforge.trace(name, input=, metadata=, tags=, user_id=, session_id=)` | One end-to-end run. Context manager (sync or `async with`). |
| `traceforge.span(name, kind, input=, metadata=, attributes=)` | A step. Nests under the current span; starts its own trace if none is active. |
| `@traceforge.observe(name=None, kind=...)` | Wraps a sync or async function: arguments in, return value out, exceptions recorded. |
| `trace.start_span(...)` / `span.end()` / `trace.end()` | Manual API when context managers do not fit. |
| `span.set_llm(model=, prompt_tokens=, completion_tokens=, total_tokens=, cost_usd=, **params)` | LLM usage. |
| `span.set_tool(name, arguments)` / `span.add_documents([...])` | Tool calls and retrieved documents (`id`, `source`, `score`, `content`). |
| `set_input` / `set_output` / `set_metadata` / `set_status` / `record_exception` | On traces and spans. |

Kinds: `llm`, `tool`, `retrieval`, `agent`, `chain`, `custom`. Exceptions leaving a span or trace mark it
`error` (type, message, traceback) and are re-raised. Current span/trace propagate through `asyncio` tasks via
`contextvars`; for threads, run the work with `contextvars.copy_context().run(...)`.

## Delivery and safety

- Events go to a bounded in-memory queue (`max_queue_size`, default 10,000) and are sent by one background
  thread in batches (`max_batch_items` 100, `max_batch_bytes` 1 MB, every `flush_interval` 1 s).
  `POST /api/v1/ingest/batch` with `Authorization: Bearer <project API key>`.
- Network errors, HTTP 408/429/5xx are retried (`max_retries` 3) with exponential backoff, honouring
  `Retry-After`. Other 4xx responses are not retried. Re-sent batches are safe: the server upserts by id.
- Telemetry never raises into your code (set `raise_on_error=True` while debugging). A full buffer drops
  events instead of blocking. `traceforge.get_client().stats()` reports sent, dropped and failed counts.
- Without an API key the client logs one warning and becomes a no-op.
- Values are made JSON-safe and redacted before they leave the process: keys such as `password`, `api_key`,
  `authorization`, `token`, `cookie`, `secret` are replaced with `[REDACTED]`, and strings matching Traceforge
  keys, bearer tokens, `sk-...` keys and AWS access key ids are masked. Add your own with
  `redact_keys=[...]` and `redact_patterns=[regex, ...]`. Fields over `max_field_bytes` (32 KB) are replaced
  by a truncated preview. `capture_inputs=False` / `capture_outputs=False` turn payload capture off.
- Values the server cannot store are converted instead of failing the batch: `NaN`/`Infinity` become the strings
  `"NaN"`/`"Infinity"`/`"-Infinity"` and NUL characters are removed. Malformed LLM usage (a non-numeric token
  count, an unprintable model) is dropped field by field; the span is still recorded.
- Redaction is pattern-based: it cannot recognise every secret or personal datum. Review what your agent records,
  and turn payload capture off where inputs or outputs may be sensitive.
- Instrumentation is explicit: spans exist only where you call `trace`, `span` or `@observe` (or the evaluation and
  experiment helpers). Nothing is patched or captured automatically.

## Evaluations

Execute a Traceforge evaluation run with your own agent. Each case runs in its own trace (tagged
`evaluation`), outputs are submitted with their trace ids, telemetry is flushed, and the run is finished so the
server scores it. Expected outputs never reach the executor. Unlike telemetry, API errors here raise
`traceforge.EvaluationError`.

```python
run = traceforge.create_evaluation_run("Support QA", name="nightly",            # or create it in the console
                                       evaluators=[{"type": "normalized_match", "field": "answer"},
                                                   {"type": "source_match"}])
finished = traceforge.evaluate(run["id"], lambda case_input: {"answer": my_agent(case_input["question"])})
print(finished["status"], finished["pass_rate"])   # pass_rate is only set for completed runs
```

`task` may be sync or async. A case whose task raises is submitted as an execution error and the run continues.
See `examples/sample_agent/evaluate.py`.

## Experiments

Replay a dataset version with a baseline and a candidate configuration of *your* agent:

```python
def task(case_input: dict, config: dict) -> dict:
    return {"answer": my_agent(case_input["question"], retriever=config["retriever"])}

experiment = traceforge.run_experiment("<experiment id>", task)   # created in the console
print(experiment["comparison"]["suggested_outcome"])
```

Each case runs once per arm in its own trace (tags `experiment`, `baseline`/`candidate`). Arms that are already scored
are skipped, so an interrupted run can be resumed. Promotion is a separate human decision in the console.
