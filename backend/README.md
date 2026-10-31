# Traceforge API (backend)

FastAPI service for console authentication, projects and project-scoped API keys (milestone M2).

## Running

```powershell
docker compose up -d postgres            # from the repository root; PostgreSQL 16 on 127.0.0.1:5433
cd backend
..\.venv\Scripts\python -m app.serve     # traceforge_dev, migrated to head if needed; console at / if built
```

Interactive docs: `http://127.0.0.1:8000/api/docs` (OpenAPI JSON at `/api/openapi.json`).

### Choosing the database

The database is chosen and name-checked before anything connects (`app/core/database.py`):

| Command | Database | Schema |
| --- | --- | --- |
| `python -m app.serve` | `traceforge_dev` (`DEV_DATABASE_URL`): **the default**, persistent local data | migrated to head on start |
| `python -m app.serve --database test` | `traceforge_test` (`TEST_DATABASE_URL`): disposable, truncated by the test suite | migrated to head on start |
| `python -m app.serve --database main` | `traceforge` (`DATABASE_URL`): **explicit only** | never migrated; refuses to start unless already at head |

- The dev/test URLs must name exactly `<main>_dev` / `<main>_test`; anything else (including the main database
  under the wrong variable) exits with code 2 before connecting. Messages name databases, never credentials.
- `uvicorn app.main:create_app --factory` and anything else calling `create_app()` without settings use
  `TRACEFORGE_DATABASE` from the *process* environment (default `dev`). It is not read from `.env`, so no file can
  silently switch a server to the main database.
- Every app start checks the schema revision and refuses a database that is not at the code's head.
- `python -m app.dev_test_server [--database dev]` still works: it is `app.serve --database test|dev`.
- `--host` accepts only loopback addresses (`127.0.0.1`, `localhost`, `::1`); requests whose `Host` is not
  `TRACEFORGE_ALLOWED_HOSTS` (default `127.0.0.1,localhost`) get 400.

### Migrations

`alembic/env.py` applies the same name guard before any migration or schema operation:

```powershell
..\.venv\Scripts\python -m alembic upgrade head                      # traceforge_dev
..\.venv\Scripts\python -m alembic -x database=test upgrade head     # traceforge_test
..\.venv\Scripts\python -m alembic -x database=main -x confirm=traceforge upgrade head   # main: back it up first
```

Without `confirm=<exact main name>` the main database is refused before connecting. Any other database name is
always refused. Programmatic callers (the test suite, `app.serve`) pass the same check.

### Serving the console

When `frontend/dist/index.html` exists (`npm run build`; location `TRACEFORGE_FRONTEND_DIST`), the API process also
serves the console: files from `dist`, and `index.html` (with a strict `Content-Security-Policy`) for client-side
routes so deep links survive a reload. `/api/...` is never handled by it (API 404/405 behaviour and the OpenAPI
schema are unchanged), missing assets are 404, and paths cannot escape `dist`. Login and registration trust the
server's own origin; that is safe because only local `Host` names are accepted (no DNS rebinding).

### Startup methods and proxy headers

The client IP used by the login rate limiter is the direct socket peer (the ASGI client address).
`X-Forwarded-For`, `X-Forwarded-Proto` and `X-Real-IP` must never be interpreted in this local-only setup:
uvicorn trusts forwarded headers from `127.0.0.1`/`::1` **by default**, and every local client connects over
loopback, so any local process could choose its own IP and get a fresh login budget per request.

| Method | Command | Proxy-header trust |
| --- | --- | --- |
| **Supported (recommended)** | `python -m app.serve [--database dev\|test\|main] [--host 127.0.0.1] [--port 8000] [--reload]` | Hard-coded off in `app/serve.py` (`proxy_headers=False`, `forwarded_allow_ips=[]`). No flag or environment variable can re-enable it. |
| Supported alternative | `python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers` (database from `TRACEFORGE_DATABASE`, default dev; does not migrate) | Off. The explicit `--no-proxy-headers` flag takes precedence over `UVICORN_PROXY_HEADERS`; `FORWARDED_ALLOW_IPS` has no effect while proxy headers are off. **Omitting the flag is unsafe.** |

Both methods are exercised by `tests/test_server_runtime.py` against real uvicorn processes, with
`FORWARDED_ALLOW_IPS=*`, `UVICORN_PROXY_HEADERS=true` and `UVICORN_FORWARDED_ALLOW_IPS=*` set to try to
re-enable trust, plus a control proving that plain `uvicorn` without the flag is spoofable. No other startup
method (gunicorn, multiple workers, a reverse proxy) is supported; one that needs a proxy must re-design client
identification and the limiter together.

## Telemetry (M3)

- `POST /api/v1/ingest/batch` — project API key (`Authorization: Bearer tf_...`). Body
  `{"traces": [...<=100], "spans": [...<=1000]}`; see `app/schemas/trace.py` for fields and limits (64 KB per
  payload field, 16 KB metadata, timezone-aware timestamps). The batch is validated and stored atomically
  (422 rejects all of it) and upserted by `(project_id, id)`, so retries are idempotent. Rows always belong to
  the key's project. Spans whose trace record has not arrived get a placeholder trace
  (`has_trace_record=false`) that the trace record later completes.
- `GET /api/v1/projects/{project_id}/traces` and `.../traces/{trace_id}` — console session; owner only (404
  otherwise). List filters: `status`, `q` (name substring or exact id), `tag`, `has_errors`, `since`, `until`;
  newest first with `limit` + `cursor` keyset pagination.
- Instrument agents with the SDK in `sdk/python`; `examples/sample_agent/agent.py` is a runnable example.

## Datasets and evaluations (M5)

- **Datasets** (`/api/v1/projects/{id}/datasets`): an editable *draft* of cases (`key`, `input`,
  `expected_output`, `expected_sources`, `metadata`), JSON/JSONL import (validated as a whole: any problem
  rejects the file and lists every error with its line) and export. `POST .../versions` freezes the draft as the
  next **immutable** version (PostgreSQL triggers reject any update to version rows); editing the draft never
  changes a published version.
- **Evaluation runs** (`/api/v1/projects/{id}/eval-runs`) reference one exact dataset version and a list of
  deterministic evaluators: `exact_match`, `normalized_match`, `contains`, `json_match`, `source_match`
  (expected sources vs. documents on the linked trace's retrieval spans), `required_fields`. Each outcome holds
  score, pass/fail, expected, actual, explanation and supporting trace/span references; links are only shown for
  traces that exist in the project.
- **Execution**: Traceforge does not run agents. An executor (the SDK's `traceforge.evaluate`) reads cases from
  `GET /api/v1/sdk/eval-runs/{id}` (no expected values), submits outputs to `.../results`, and calls
  `.../finish` (project API key). State machine, enforced by the API with row locks *and* by database triggers:
  `pending → running → scoring → completed | incomplete | failed` (plus abort to `failed`). Missing cases give
  `incomplete`; a scoring exception gives `failed` with no partial scores kept; results cannot be written once
  a run is terminal. Pass rates are reported for `completed` runs only. Scoring runs synchronously in the
  finish request (fine for local datasets; large datasets would need a worker).

## Failure diagnosis (M6)

`POST /api/v1/projects/{id}/eval-runs/{run_id}/diagnoses` investigates the failed cases of a scored run (owner only,
CSRF) and runs in the background; poll `GET .../diagnoses/{diagnosis_id}`. Re-running (`.../retry`) creates a new
diagnosis linked to the previous one; finished diagnoses are immutable (database triggers).

**Workflow** (`app/diagnosis/workflow.py`) is an explicit, typed state machine:
`load_run → collect_failed_cases → gather_traces → analyze_execution → compare_outputs → propose_hypotheses →
assess_evidence → score_confidence → identify_missing_evidence → recommend_experiments → persist`. It threads one
Pydantic `WorkflowState`, validates every transition against a declared table, and is bounded by a step cap, an
overall time limit (`TRACEFORGE_DIAGNOSIS_TIME_LIMIT_SECONDS`), a case limit (`TRACEFORGE_DIAGNOSIS_MAX_CASES`) and
deadline-aware provider retries (`TRACEFORGE_DIAGNOSIS_PROVIDER_MAX_RETRIES`, transient and malformed-output errors only).
*LangGraph is not a dependency*: every current LangGraph release pulls in `httpx2`, which would switch Starlette's
test client away from the pinned `httpx`; the state machine keeps the same graph semantics without it.

**Evidence and its limits.**
- Evidence is read through allowlisted functions (`app/diagnosis/evidence.py`) in a `READ ONLY` transaction, filtered
  by the run's project and bounded (200 spans per trace, 60 facts per case). The diagnosis cannot write, run SQL,
  shell commands, HTTP requests or tools.
- Facts are computed deterministically (evaluator outcomes, retrieved sources and ranks, answer/document and
  answer/tool lexical overlap, tool and LLM spans, evidence gaps). Trace inputs, documents and outputs are untrusted:
  they are quoted into facts and never interpreted.
- A provider proposes hypotheses citing fact ids only. The server then decides each hypothesis's
  **evidence status** (`supported`, `unsupported`, `contradicted`, `inconclusive`) with fixed per-category rules,
  caps its confidence accordingly (0.9 / 0.5 / 0.15 / none), drops citations of unknown facts, and derives trace and
  span references only from the cited facts, so every reference resolves to a stored row.
- `supported` means the stored evidence matches the cause's pattern; it is still a hypothesis.
  **Validation status** stays `unvalidated` until an experiment (M7) validates it; the database requires an
  experiment reference for `validated`.
- A failed case without a usable trace yields `insufficient_evidence` (inconclusive), never a guessed cause.

**Providers** (`TRACEFORGE_DIAGNOSIS_PROVIDER`):

| Value | Behaviour |
| --- | --- |
| `mock` (default) | Deterministic, offline rule-based reasoner over the extracted signals. No key needed. |
| `anthropic` | Claude via the official `anthropic` SDK (`messages.parse` with the `ProviderOutput` schema). Model: `TRACEFORGE_DIAGNOSIS_MODEL` (default `claude-opus-5-5`); key: `ANTHROPIC_API_KEY` (server-side only). The SDK is an optional dependency (`pip install -e "backend[anthropic]"`) and is **not installed** in the current environment, because it also requires `httpx2`. Refusals, truncation, timeouts, rate limits and auth errors are mapped to typed provider errors; credentials never appear in errors or logs. |

## Controlled experiments (M7)

An experiment replays one **exact dataset version** with a **baseline** and a **candidate** configuration and
compares them. It is created from a *completed* evaluation run (`POST /api/v1/projects/{id}/experiments`), takes that
run's dataset version and evaluators, records the configuration diff, and creates two ordinary evaluation runs (the
arms), so scoring and result immutability are M5's.

- **Execution** stays with your code: `traceforge.run_experiment(experiment_id, task)` calls `task(case_input, config)`
  for every case with each arm's configuration (each case in its own trace tagged `experiment` and the arm), then asks
  the server to complete the experiment (`/api/v1/sdk/experiments/{id}/start|complete`, project API key). Traceforge
  never modifies or deploys an agent. `examples/sample_agent/experiment.py` shows a configurable agent
  (`retriever`: keyword | stemmed, `context`: first | query_overlap).
- **States** `pending → running → completed | failed` (abort → failed), enforced by the API with row locks and by
  database triggers, which also require both arms to use the experiment's project and dataset version and keep the
  definition immutable. Bounds: `TRACEFORGE_EXPERIMENT_MAX_CASES` and a deadline of
  `TRACEFORGE_EXPERIMENT_TIME_LIMIT_SECONDS` from start; an arm run that failed fails the experiment.
- **Comparison** (stored on completion): pass rates and their delta (only when both arms are `completed`),
  evaluator pass rates, retrieval recall from `source_match`, fixed / regressed / unchanged cases, evaluator-level
  changes, and latency, tokens and cost compared only over cases measured in *both* arms (reported as not measured
  otherwise). The "suggested outcome" is a computed suggestion, never a decision. `GET .../experiments/{id}/cases`
  gives per-case baseline vs candidate with trace links.
- **Hypotheses**: an experiment may test a diagnosis hypothesis from the source run. The diagnosis's recommended
  experiment is shown as context only. `POST .../validate-hypothesis` marks the hypothesis `validated` (by
  `experiment:<id>`) only if the baseline arm reproduced the failure of that case and the candidate fixed it.
- **Promotion**: `POST .../decisions` records an approve/reject decision with a required rationale (append-only,
  audited, only on completed experiments). It changes no agent, configuration or deployment.

## Recovery of interrupted work (M8)

Everything runs in one local process; there is no job queue. Work interrupted by a crash or restart is detected
by `app/services/recovery.py`, which runs once at startup and then every `TRACEFORGE_RECOVERY_INTERVAL_SECONDS`
(default 60; 0 = startup only):

| Left behind | Detected when | Result |
| --- | --- | --- |
| Diagnosis `pending` (task never started) | older than `TRACEFORGE_STALE_JOB_GRACE_SECONDS` (120) | `failed`, "Interrupted: … never started"; *Run again* creates a new diagnosis |
| Diagnosis `running` (worker died) | running longer than time limit + provider timeout + grace | `failed`, "Interrupted: … stopped without finishing"; no partial cases are kept |
| Evaluation run `scoring` (scorer died) | not row-locked (a live scorer holds the lock) and older than the grace | `failed`; scores of the interrupted attempt were rolled back; re-run it |
| Experiment `running` | past `deadline_at` | `failed` with its unfinished arm runs, which stop accepting results |

Transitions use the same state machines and database triggers as requests, with `SKIP LOCKED`, so a sweep never
blocks or races a live transition, never touches finished records, and a live worker that loses the race to it
writes nothing (`persist` re-checks the status under the row lock). Pending experiments and pending/running
evaluation runs wait for *your* executor and are not touched: an idle executor cannot be told from a dead one. An
interrupted `run_experiment` can simply be started again before the deadline; arms already scored are skipped.
Until a sweep runs, interrupted work keeps showing its last state (at most about a minute plus the grace period).

## Tests

```powershell
cd backend
..\.venv\Scripts\python -m pytest
```

The suite is integration-only against real PostgreSQL. It refuses to start unless `TEST_DATABASE_URL`
names exactly `<DATABASE_URL database>_test` (`traceforge_test`). Each run migrates that database down to
base and back up to head, and truncates the M2 tables before every test. It never connects to `traceforge`.

## Security model

| Concern | Behaviour |
| --- | --- |
| Passwords | argon2id. Login verifies a dummy hash for unknown emails so timing does not reveal registered addresses. |
| Emails | Registration and login both use one canonical form (trimmed, Unicode NFC, canonical domain, lowercased), so an address typed in another Unicode normalization still matches; the database enforces `email = lower(email)`. Registration answers 409 for a taken email, which reveals that the address has an account (inherent to open self-registration). |
| Sessions | `tf_session` HttpOnly, SameSite=Lax cookie. Only the token's SHA-256 is stored. Sessions expire after `TRACEFORGE_SESSION_TTL_HOURS` and are revoked server-side on logout. |
| CSRF (authenticated) | Every state-changing request authenticated by the session cookie must send `X-CSRF-Token`, an HMAC of the session token. |
| CSRF (login/register) | These endpoints accept only `application/json` (415 otherwise), which no HTML form can send, and reject any `Origin` not listed in `TRACEFORGE_CORS_ORIGINS` (403). Without `Origin`, the `Referer` origin must be trusted. With neither header the request is allowed: browsers always send `Origin` on cross-origin POSTs, so such requests come from non-browser clients (curl, scripts, tests). |
| Ownership | Every project-scoped route checks ownership in the query; SDK routes are scoped to the key's project and telemetry ids are unique per project. Another user's resources return 404, identical to missing ones. `tests/test_isolation.py` calls every route in the OpenAPI schema with another user's real ids (in their project and in the caller's own) and checks for 404 and unchanged data. |
| API keys | Format `tf_<8-hex prefix>_<secret>`. The full key is returned once, at creation; only an HMAC-SHA256 (keyed with `TRACEFORGE_API_KEY_PEPPER`) is stored. |
| Audit log | `api_key.created`, `api_key.revoked`, `project.deleted`. Details hold names and key prefixes only, never secrets, hashes or tokens. |
| Logs and errors | Log lines are scrubbed of API keys, bearer tokens, session cookies and password fields; validation errors never echo submitted values. Every error (including 413 from the size limit and unhandled 500s) uses the error envelope with `request_id`, the `X-Request-ID` header and CORS headers. Values PostgreSQL cannot store (NaN/Infinity in JSON, NUL characters) are a 422 `unstorable_value`, not a 500. |
| Response headers | Every response sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cache-Control: no-store`. |
| Untrusted content | Trace, dataset and document content is stored and displayed as data: React escapes it, nothing is rendered as HTML, and the diagnosis workflow quotes it to the provider and never executes it. Diagnosis evidence is read through allowlisted queries in a read-only transaction; neither diagnoses nor experiments run shell commands, arbitrary SQL, file access or outbound network calls (other than the optional Anthropic provider). Experiments are executed by *your* process through the SDK. |

### API keys do not expire

API keys have no expiry. A key stays valid until it is revoked manually
(`DELETE /api/v1/projects/{project_id}/api-keys/{key_id}`), its project is deleted, or its owner is
deactivated. Revocation takes effect on the next request. Rotate keys by creating a new key, switching
clients over, then revoking the old one.

### Failed-login rate limiting

After `TRACEFORGE_LOGIN_MAX_FAILURES` failed logins for the same (client IP, email), or
`TRACEFORGE_LOGIN_MAX_FAILURES_PER_IP` failed logins from one IP across all emails, within a sliding
`TRACEFORGE_LOGIN_FAILURE_WINDOW_SECONDS` window, login responds `429 too_many_attempts` with a
`Retry-After` header (exposed to the console via CORS) and `details.retry_after_seconds`. This applies even
when the password is correct. Throttled requests do no password hashing. There is no account lockout: other
IPs can still log in to the same account.

Each login atomically *reserves* an attempt in both budgets, under one lock, before verifying the password.
In-flight reservations count against the budget, so simultaneous requests cannot exceed it. A failed login
turns its reservation into a recorded failure; a successful one releases it and clears that (IP, email)
budget, but keeps the IP-wide failures. An unexpected exception releases the reservation, and a reservation
that is somehow never finalized expires after 60 seconds. Consequence: more simultaneous attempts for one
(IP, email) than the budget allows get `429` with `Retry-After: 1` even if their passwords are right.

Memory is bounded: each budget tracks at most `TRACEFORGE_LOGIN_LIMITER_MAX_KEYS` keys. To make room for a
new key, the least recently used key **without in-flight reservations** is evicted (expired reservations
are dropped first), and its failure history is lost. A key with in-flight reservations is never evicted. If
every tracked key has in-flight reservations, the new login is refused with `429` and `Retry-After: 1`, and
nothing is reserved in either budget.

**Limitations: the limiter is in-process and suitable only for the current single-process local deployment.**

- State is per Python process. It is not shared between uvicorn workers, processes or hosts, so N workers
  give an attacker N times the budget. It also resets when the process restarts.
- Evicting an idle key forgets its failures. An attacker able to create more than
  `TRACEFORGE_LOGIN_LIMITER_MAX_KEYS` new keys within one window could reset idle budgets this way. With
  the defaults and loopback-only clients this is not reachable: the per-IP budget caps new keys at about 50
  per IP per window.
- The 60-second reservation TTL is not an upper bound on a login: connection waits are bounded (pool 30s,
  connect 10s) but query time is not. A login that outlives the TTL frees its slot early, so one more
  attempt can be reserved for that key: a temporary overshoot of one attempt per stalled request. Its late
  failure is still recorded and it never affects another request's reservation.
- The client IP is the direct peer address, provided the server is started by one of the supported methods
  above. Behind a reverse proxy every client would share the proxy's IP.
- **Local clients share one IP budget.** In this local-only deployment every client (browser, scripts, other
  local processes) connects from loopback (`127.0.0.1` or `::1`), so all of them share one per-IP budget
  and the (IP, email) budget is effectively per email. Two consequences, both accepted for now:
  - A successful login for an email clears that email's failures for every local client, including
    failures caused by someone else guessing it. The per-IP budget is not cleared, so total guessing stays
    capped at `TRACEFORGE_LOGIN_MAX_FAILURES_PER_IP` per window.
  - Excessive failed logins from any local process can exhaust the shared per-IP budget and temporarily
    throttle every local user (`429` until the window slides). This is temporary throttling, not account
    lockout.

A multi-worker or distributed deployment must replace it with a shared store (for example Redis or
PostgreSQL) behind the same `reserve` / `fail` / `succeed` / `release` interface.
