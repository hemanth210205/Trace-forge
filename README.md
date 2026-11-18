# Traceforge

**See what your AI agent is doing, find out why it fails, and prove that a fix works.**

Traceforge is a local-first platform for observing, evaluating and debugging AI agents. You add a few lines of the
Python SDK to your agent, and Traceforge records every step it takes: each LLM call, tool call and document
retrieval. From there you can test the agent on saved datasets, get evidence-backed explanations for failed cases,
and run controlled experiments that compare your current agent with a changed version, side by side.

Traceforge runs entirely on your own machine. It never changes or deploys your agent. Every decision to adopt a
change is made by a person.

---

## Table of contents

1. [How it works](#how-it-works)
2. [Tech stack](#tech-stack)
3. [Features](#features)
4. [Roadmap](#roadmap)
5. [Project structure](#project-structure)
6. [Getting started](#getting-started)
7. [Configuration](#configuration)
8. [Databases and migrations](#databases-and-migrations)
9. [Using the web console](#using-the-web-console)
10. [Python SDK guide](#python-sdk-guide)
11. [Evaluations](#evaluations)
12. [Failure diagnosis](#failure-diagnosis)
13. [Controlled experiments](#controlled-experiments)
14. [REST API reference](#rest-api-reference)
15. [Security model](#security-model)
16. [Recovery of interrupted work](#recovery-of-interrupted-work)
17. [Backup and restore](#backup-and-restore)
18. [Running the tests](#running-the-tests)
19. [Supported deployment](#supported-deployment)
20. [Known limitations](#known-limitations)
21. [Author](#author)
22. [License](#license)

---

## How it works

```
 Your agent + Traceforge Python SDK
            │  traces: LLM calls, tool calls, retrievals
            ▼
 ┌────────────────────────────────┐        ┌───────────────┐
 │  Traceforge API (FastAPI)      │◄──────►│ PostgreSQL 16 │
 │  ingest · evaluate · diagnose  │        │   (Docker)    │
 │  · experiment                  │        └───────────────┘
 └───────────────┬────────────────┘
                 ▼
      Web console (React) at http://127.0.0.1:8000
```

Traceforge follows a four-step loop:

1. **Trace**: instrument your agent with the SDK. Every run becomes a *trace* made of *spans* (one span per step).
2. **Evaluate**: run your agent on a versioned dataset of test cases. Each answer is scored by deterministic
   evaluators.
3. **Diagnose**: for the cases that failed, Traceforge collects evidence from the traces and proposes
   *hypotheses* about the cause, such as a retrieval failure or a context selection failure.
4. **Experiment**: run a *baseline* and a *candidate* configuration of your agent on the same dataset, compare
   them case by case, validate or reject a hypothesis, and record a human approve/reject decision.

---

## Tech stack

| Layer | Technologies |
| --- | --- |
| **Backend API** | Python 3.11, FastAPI, Uvicorn, Pydantic v2, pydantic-settings |
| **Database** | PostgreSQL 16 (Docker), SQLAlchemy 2.0, Alembic migrations, psycopg 3 |
| **Security** | Argon2id password hashing, HttpOnly session cookies, CSRF tokens, HMAC-hashed API keys, login rate limiting |
| **Web console** | React 18, TypeScript, Vite, React Router, Tailwind CSS |
| **Python SDK** | Pure Python 3.10+, no runtime dependencies, background batching with retries |
| **Diagnosis providers** | Deterministic offline `mock` provider (default); optional Anthropic Claude provider (experimental) |
| **Testing** | pytest, httpx, Vitest, Testing Library, jsdom, Playwright (end-to-end in a real browser) |
| **Tooling** | Docker Compose, npm, setuptools |

---

## Features

### Tracing and Trace Explorer
- Python SDK with `trace`, `span` and `@observe` APIs, for both sync and async code.
- Records LLM calls (model, tokens, cost), tool calls, retrieved documents, inputs, outputs and errors.
- Non-blocking delivery with a bounded queue, batching and retries; telemetry never crashes your agent.
- Redacts passwords, tokens, API keys and similar values before data leaves your process.
- Trace Explorer with search, filters, a span tree, a waterfall timeline and a detail panel for every step.

### Datasets and evaluation
- Datasets with an editable draft and **immutable published versions**, so results always refer to the exact
  cases they were run on.
- JSON and JSONL import and export, with every error listed by line.
- Deterministic evaluators: `exact_match`, `normalized_match`, `contains`, `json_match`, `required_fields`,
  `source_match`.
- Per-case results with expected vs actual output, an explanation and links to the trace.

### Diagnosis
- An 11-step diagnosis workflow that analyses each failed case and its trace.
- Seven failure categories plus *insufficient evidence*: retrieval, context selection, grounding, tool execution,
  tool selection, reasoning and output format failures.
- Each hypothesis lists supporting and contradicting facts, a confidence level, missing evidence and a recommended
  experiment, and links to the exact spans it is based on.
- Hypotheses stay **unvalidated** until an experiment validates them. This rule is enforced in the database.

### Experiments
- Compare a baseline and a candidate configuration on the same dataset version and evaluators.
- See pass rates, fixes, regressions, retrieval recall, latency, tokens and cost, case by case.
- Validate a diagnosis hypothesis when the baseline reproduces the failure and the candidate fixes it.
- Append-only, audited approve/reject decisions with a written rationale.

### Accounts, security and reliability
- User accounts, projects, project-scoped API keys and an audit log.
- Strict isolation: a user can never see or change another user's data.
- Automatic recovery of work interrupted by a crash or restart.
- Login rate limiting, CSRF and Origin checks, Content-Security-Policy and secure response headers.
- Safe database handling: separate `dev`, `test` and `main` databases; the main one is never used by default.

---

## Roadmap

Planned improvements for future releases:

- [ ] **Network and team deployment**: HTTPS, team workspaces and running several API workers.
- [ ] **Framework integrations**: automatic tracing for popular LLM and agent frameworks.
- [ ] **LLM-as-judge evaluators**: scoring answers by meaning, not only by fixed rules.
- [ ] **Smarter diagnosis**: semantic (embedding-based) grounding checks in place of word overlap, and full
      testing of the Anthropic Claude provider against the live API.
- [ ] **Richer experiments**: more than two arms, repeated trials and statistical significance.
- [ ] **Account management**: password reset, profile settings and expiring API keys.
- [ ] **Data management**: retention policies, automated backups and trace export.
- [ ] **More SDKs**, starting with TypeScript/JavaScript.
- [ ] **Packaging**: a one-command Docker setup for the whole stack, and the SDK published on PyPI.

---

## Project structure

```
Traceforge/
├── backend/                  FastAPI application
│   ├── app/
│   │   ├── api/routes/       REST API endpoints
│   │   ├── core/             Settings, security, middleware, rate limiting, database selection
│   │   ├── db/               SQLAlchemy base, sessions and schema checks
│   │   ├── diagnosis/        Diagnosis workflow, evidence collection and providers
│   │   ├── models/           Database models
│   │   ├── schemas/          Pydantic request and response models
│   │   ├── services/         Ingestion, evaluation, experiments, recovery, audit
│   │   ├── main.py           App factory
│   │   └── serve.py          Supported way to start the server
│   ├── alembic/              Database migrations
│   ├── tests/                Integration tests (real PostgreSQL)
│   ├── pyproject.toml
│   └── constraints.txt       Pinned, tested dependency versions
├── frontend/                 React + TypeScript web console
│   ├── src/                  Pages, components, API client
│   ├── e2e/                  Playwright end-to-end tests
│   └── e2e-release/          Release startup test
├── sdk/python/               Traceforge Python SDK
├── examples/sample_agent/    Instrumented sample customer-support agent
├── docker/postgres/init/     Scripts that create the dev and test databases
├── docs/                     Release checklist, backup and restore guide
├── docker-compose.yml        PostgreSQL 16 container
└── .env.example              Configuration template
```

---

## Getting started

### Prerequisites

| Tool | Version | Used for |
| --- | --- | --- |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Any recent | Runs PostgreSQL 16 |
| [Python](https://www.python.org/downloads/) | 3.11 | Backend (the SDK supports 3.10+) |
| [Node.js](https://nodejs.org/) and npm | 20 or newer | Builds the web console |
| Microsoft Edge | Any recent | Only for the end-to-end tests |

The commands below are for **Windows PowerShell**.

### 1. Clone the repository

```powershell
git clone https://github.com/<your-username>/traceforge.git
cd traceforge
```

### 2. Create your environment file

```powershell
Copy-Item .env.example .env
```

Open `.env` and set:
- `POSTGRES_PASSWORD`, and the same password inside the three `*_DATABASE_URL` lines.
- `TRACEFORGE_SECRET_KEY` and `TRACEFORGE_API_KEY_PEPPER`, which must be two **different** random values of at
  least 32 characters.

Generate each secret with:

```powershell
py -3.11 -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Start PostgreSQL

```powershell
docker compose up -d postgres
```

The first start creates three databases: `traceforge`, `traceforge_dev` and `traceforge_test`. PostgreSQL listens
on `127.0.0.1:5433`, so it does not clash with a native PostgreSQL on port 5432.

### 4. Install the backend and the SDK

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e "backend[dev]" -c backend\constraints.txt
.venv\Scripts\python -m pip install -e sdk\python
```

### 5. Build the web console

```powershell
cd frontend
npm ci
npm run build
cd ..
```

### 6. Start Traceforge

```powershell
cd backend
..\.venv\Scripts\python -m app.serve
```

Open **http://127.0.0.1:8000** and create an account. The first start migrates the `traceforge_dev` database
automatically. Interactive API docs are at **http://127.0.0.1:8000/api/docs**. Stop the server with `Ctrl+C`.

### 7. Try the sample agent

1. In the console, create a project and generate an API key. Copy it: it is shown only once.
2. Send traces from the sample agent (from the repository root):

   ```powershell
   $env:TRACEFORGE_API_KEY = "tf_..."
   .venv\Scripts\python examples\sample_agent\agent.py --endpoint http://127.0.0.1:8000
   ```

3. Open the project's **Traces** tab to explore them.
4. `examples/sample_agent/evaluate.py` runs an evaluation on the sample dataset
   (`support_qa.jsonl`), and `examples/sample_agent/experiment.py` runs an experiment.

The sample agent runs offline: it uses keyword retrieval over a small knowledge base, an order-lookup tool and a
deterministic template "model", so its traces are repeatable.

### Development mode (hot reload)

```powershell
# Terminal 1: API on http://127.0.0.1:8000
cd backend
..\.venv\Scripts\python -m app.serve --reload

# Terminal 2: console on http://localhost:5173 (forwards /api to the API)
cd frontend
npm install
npm run dev
```

Set `TRACEFORGE_API_URL` to point the dev console at a different API address.

---

## Configuration

All settings live in `.env` in the repository root. [.env.example](.env.example) is the template.
**Never commit `.env`**: it holds your database password and secret keys, and it is already in `.gitignore`.

### PostgreSQL (Docker)

| Variable | Description | Default |
| --- | --- | --- |
| `POSTGRES_USER` | Database user | `traceforge` |
| `POSTGRES_PASSWORD` | Database password | *required* |
| `POSTGRES_DB` | Main database name | `traceforge` |
| `POSTGRES_HOST_PORT` | Host port for the container | `5433` |

### Database connections

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | Main database. Used only with `--database main`. Its name is the base for the two below. |
| `DEV_DATABASE_URL` | Default database for `python -m app.serve`. The name must be exactly `<main>_dev`. |
| `TEST_DATABASE_URL` | Test suite and `--database test`. The name must be exactly `<main>_test`. |

Use `127.0.0.1` in these URLs, not `localhost`: on Windows, `localhost` tries IPv6 first, where the container is
not listening.

### Security and sessions

| Variable | Description | Default |
| --- | --- | --- |
| `TRACEFORGE_SECRET_KEY` | Signs CSRF tokens. At least 32 characters. Changing it signs everyone out. | *required* |
| `TRACEFORGE_API_KEY_PEPPER` | Secret used to hash API keys. At least 32 characters. Changing it invalidates every API key. | *required* |
| `TRACEFORGE_SESSION_TTL_HOURS` | Session lifetime | `168` (7 days) |
| `TRACEFORGE_COOKIE_SECURE` | Must stay `false` for plain HTTP on localhost | `false` |
| `TRACEFORGE_MAX_REQUEST_BYTES` | Largest accepted request body | `5242880` (5 MiB) |
| `TRACEFORGE_CORS_ORIGINS` | Browser origins allowed to call the API (the Vite dev server). Origins are exact. | `http://localhost:5173` |
| `TRACEFORGE_ALLOWED_HOSTS` | Host names the server answers to. Others get 400 (this blocks DNS rebinding). | `127.0.0.1,localhost` |
| `TRACEFORGE_FRONTEND_DIST` | Folder of the built console. Empty disables serving it. | `frontend/dist` |
| `TRACEFORGE_LOG_LEVEL` | Log level | `INFO` |

### Google and GitHub sign-in

Optional. A provider's button is enabled only when both its client id and secret are set; otherwise it is shown
disabled.

| Variable | Description | Default |
| --- | --- | --- |
| `TRACEFORGE_GITHUB_CLIENT_ID`, `TRACEFORGE_GITHUB_CLIENT_SECRET` | A GitHub OAuth App | *unset* |
| `TRACEFORGE_GOOGLE_CLIENT_ID`, `TRACEFORGE_GOOGLE_CLIENT_SECRET` | A Google OAuth client (Web application) | *unset* |
| `TRACEFORGE_PUBLIC_URL` | The origin your browser uses for the console. Use `http://localhost:5173` with `npm run dev`. | `http://127.0.0.1:8000` |

Register `<TRACEFORGE_PUBLIC_URL>/api/v1/auth/oauth/github/callback` (or `.../google/callback`) as the redirect URL
with the provider. Only provider-verified emails are accepted. A first sign-in creates an account without a password,
or links the existing account with that email; afterwards the provider account is recognised by its id. The flow uses
a signed, 10-minute `state` cookie (and PKCE for Google), and returns only to local paths.

### Login rate limiting

| Variable | Description | Default |
| --- | --- | --- |
| `TRACEFORGE_LOGIN_MAX_FAILURES` | Failed logins allowed per (IP, email) within the window | `5` |
| `TRACEFORGE_LOGIN_MAX_FAILURES_PER_IP` | Failed logins allowed per IP across all emails | `50` |
| `TRACEFORGE_LOGIN_FAILURE_WINDOW_SECONDS` | Length of the sliding window | `900` (15 min) |
| `TRACEFORGE_LOGIN_LIMITER_MAX_KEYS` | Most (IP, email) pairs tracked in memory | `10000` |

### Diagnosis settings

| Variable | Description | Default |
| --- | --- | --- |
| `TRACEFORGE_DIAGNOSIS_PROVIDER` | `mock` (offline, deterministic) or `anthropic` (experimental) | `mock` |
| `TRACEFORGE_DIAGNOSIS_MODEL` | Claude model used by the `anthropic` provider | `claude-opus-5-5` |
| `ANTHROPIC_API_KEY` | API key for the `anthropic` provider. Server-side only. | — |
| `TRACEFORGE_DIAGNOSIS_PROVIDER_TIMEOUT_SECONDS` | Timeout per provider call | `60` |
| `TRACEFORGE_DIAGNOSIS_PROVIDER_MAX_RETRIES` | Retries for temporary provider errors | `2` |
| `TRACEFORGE_DIAGNOSIS_MAX_CASES` | Most failed cases analysed per diagnosis | `10` |
| `TRACEFORGE_DIAGNOSIS_TIME_LIMIT_SECONDS` | Time limit for one diagnosis | `300` |

### Experiments and recovery

| Variable | Description | Default |
| --- | --- | --- |
| `TRACEFORGE_EXPERIMENT_MAX_CASES` | Most cases per experiment | `500` |
| `TRACEFORGE_EXPERIMENT_TIME_LIMIT_SECONDS` | Time allowed from start to completion | `3600` (1 h) |
| `TRACEFORGE_RECOVERY_INTERVAL_SECONDS` | How often to check for interrupted work (`0` = only at startup) | `60` |
| `TRACEFORGE_STALE_JOB_GRACE_SECONDS` | How long queued work may wait before it counts as abandoned | `120` |

### Server command options

| Command | Effect |
| --- | --- |
| `python -m app.serve` | Uses `traceforge_dev` (default) and migrates it if needed |
| `python -m app.serve --database test` | Uses the disposable `traceforge_test` database |
| `python -m app.serve --database main` | Uses the main database. It is never migrated automatically. |
| `python -m app.serve --port 8001` | Changes the port (default `8000`) |
| `python -m app.serve --reload` | Restarts on code changes (development) |

`--host` accepts only loopback addresses: `127.0.0.1`, `localhost` or `::1`.

---

## Databases and migrations

Traceforge keeps three databases apart so that tests and experiments never touch your real data:

| Database | Purpose | Used by |
| --- | --- | --- |
| `traceforge_dev` | Your persistent local data | `python -m app.serve` (default) |
| `traceforge_test` | Disposable; wiped by the test suite | Tests, `--database test` |
| `traceforge` | Main database | Only `--database main`, explicitly |

Safety rules:
- The database is chosen and its name is checked **before** anything connects. A URL that names the wrong
  database stops the server with exit code 2.
- Every start checks the schema version and refuses a database that is not up to date.
- The main database is never migrated by the server. You must migrate it yourself, after a backup.

Run migrations from `backend/`:

```powershell
..\.venv\Scripts\python -m alembic upgrade head                                          # traceforge_dev
..\.venv\Scripts\python -m alembic -x database=test upgrade head                         # traceforge_test
..\.venv\Scripts\python -m alembic -x database=main -x confirm=traceforge upgrade head   # main: back it up first
```

To start using the main database: stop every Traceforge server, back it up (see
[Backup and restore](#backup-and-restore)), run the third command above, then start with
`python -m app.serve --database main`.

---

## Using the web console

| Screen | What you can do |
| --- | --- |
| **Projects** | Create and open projects. Each project has its own traces, datasets, runs and keys. |
| **API keys** | Create a key (the secret is shown once), see when each key was last used, revoke keys. |
| **Traces** | Browse traces, newest first. Search by name or id; filter by status, time range, tag or "spans with errors". Filters are kept in the URL. |
| **Trace detail** | Summary (status, duration, errors, tokens, cost, user, session), a span timeline with a waterfall view, and a detail panel per span: errors and stack traces, model and tokens, tool arguments, retrieved documents, inputs and outputs. |
| **Datasets** | Create datasets, add and edit cases, import and export JSON/JSONL, publish immutable versions. |
| **Evaluations** | Start, abort and re-run evaluation runs; see pass rates and per-case results linked to traces. |
| **Diagnoses** | Diagnose a scored run's failures and review facts, hypotheses and validated causes, with links to spans. |
| **Experiments** | Create experiments, compare baseline vs candidate, validate hypotheses and record decisions. |

All trace content is displayed as plain text and is never run as code or HTML.

---

## Python SDK guide

Install the SDK into your agent's environment:

```powershell
pip install -e sdk\python      # from the Traceforge repository
```

### Quick example

```python
import traceforge

traceforge.init(api_key="tf_...", endpoint="http://127.0.0.1:8000")
# or set the TRACEFORGE_API_KEY and TRACEFORGE_ENDPOINT environment variables

@traceforge.observe(kind="tool")
def lookup_order(order_id: str) -> dict:
    ...

with traceforge.trace("support-request", input={"question": q}, tags=["prod"], user_id="u-42") as t:
    with traceforge.span("kb-search", kind="retrieval", input=q) as s:
        s.add_documents([{"id": "doc-7", "source": "kb/shipping.md", "score": 0.91}])

    order = lookup_order("A1")

    with traceforge.span("answer", kind="llm", input=prompt) as llm:
        reply = call_model(prompt)
        llm.set_llm(model="your-model", prompt_tokens=812, completion_tokens=64, cost_usd=0.00021)
        llm.set_output(reply)

    t.set_output(reply)

traceforge.shutdown()   # sends any remaining data; also runs automatically when Python exits
```

### API

The SDK records only what you instrument. It does not hook into LLM or agent frameworks automatically.

| API | Use |
| --- | --- |
| `traceforge.trace(name, input=, metadata=, tags=, user_id=, session_id=)` | One end-to-end run. Context manager (`with` or `async with`). |
| `traceforge.span(name, kind, input=, metadata=, attributes=)` | One step. Nests under the current span; starts its own trace if none is active. |
| `@traceforge.observe(name=None, kind=...)` | Wraps a sync or async function: arguments in, return value out, exceptions recorded. |
| `trace.start_span(...)`, `span.end()`, `trace.end()` | Manual API when context managers do not fit. |
| `span.set_llm(model=, prompt_tokens=, completion_tokens=, total_tokens=, cost_usd=, **params)` | LLM usage. |
| `span.set_tool(name, arguments)` | Tool calls. |
| `span.add_documents([...])` | Retrieved documents (`id`, `source`, `score`, `content`). |
| `set_input`, `set_output`, `set_metadata`, `set_status`, `record_exception` | On traces and spans. |

Span kinds: `llm`, `tool`, `retrieval`, `agent`, `chain`, `custom`. An exception that leaves a span or trace marks
it as `error` (with type, message and traceback) and is re-raised. The current trace and span follow `asyncio`
tasks automatically; for threads, use `contextvars.copy_context().run(...)`.

### Delivery

- Events go into an in-memory queue (default 10,000 items) and are sent by a background thread in batches
  (up to 100 items or 1 MB, every second) to `POST /api/v1/ingest/batch`.
- Network errors and HTTP 408, 429 and 5xx responses are retried up to 3 times with backoff, respecting
  `Retry-After`. Re-sent batches are safe because the server de-duplicates by id.
- Telemetry never raises errors into your code (use `raise_on_error=True` while debugging). If the queue is full,
  events are dropped instead of blocking your agent.
- `traceforge.get_client().stats()` reports sent, dropped and failed counts.
- Without an API key, the SDK logs one warning and does nothing.

### Privacy and redaction

- Keys such as `password`, `api_key`, `authorization`, `token`, `cookie` and `secret` are replaced with
  `[REDACTED]`. Strings that look like Traceforge keys, bearer tokens, `sk-...` keys or AWS access key ids are
  masked.
- Add your own rules with `redact_keys=[...]` and `redact_patterns=[regex, ...]`.
- Fields larger than 32 KB are replaced by a shortened preview.
- `capture_inputs=False` and `capture_outputs=False` turn off payload capture entirely.
- Redaction is pattern-based and cannot catch every secret or personal detail, so review what your agent records.

---

## Evaluations

An evaluation run tests your agent on one **exact dataset version** with a list of evaluators.

### Evaluators

| Evaluator | Passes when |
| --- | --- |
| `exact_match` | The output equals the expected output exactly |
| `normalized_match` | They match after normalising case, spacing and punctuation |
| `contains` | The output contains the expected text |
| `json_match` | The output and the expected output are equal JSON values |
| `required_fields` | The output contains all required fields |
| `source_match` | The trace's retrieval spans found the expected source documents |

### Running an evaluation from your code

Traceforge does not run your agent. Your code (the *executor*) runs each case and sends back the output:

```python
run = traceforge.create_evaluation_run(
    "Support QA", name="nightly",
    evaluators=[{"type": "normalized_match", "field": "answer"}, {"type": "source_match"}],
)
finished = traceforge.evaluate(run["id"], lambda case_input: {"answer": my_agent(case_input["question"])})
print(finished["status"], finished["pass_rate"])
```

- Each case runs in its own trace, tagged `evaluation`. Expected outputs are never sent to the executor.
- The task can be sync or async. If it raises, that case is recorded as an execution error and the run continues.
- Run states: `pending → running → scoring → completed | incomplete | failed`. A run with missing cases is
  `incomplete`; a pass rate is reported only for `completed` runs.

---

## Failure diagnosis

Click **Diagnose failures** on a scored evaluation run, or call the API. The diagnosis runs in the background and
investigates each failed case.

### Workflow

`load run → collect failed cases → gather traces → analyse execution → compare outputs → propose hypotheses →
assess evidence → score confidence → identify missing evidence → recommend experiments → save`

Each diagnosis is limited by a case count, a time limit and a retry budget (see [Diagnosis settings](#diagnosis-settings)).

### How evidence is handled

- Evidence is read through a fixed set of read-only queries, limited to the run's project. A diagnosis cannot
  write data, run SQL or shell commands, or make network calls.
- Facts are computed deterministically: evaluator results, retrieved sources and their ranks, word overlap between
  the answer and documents or tool results, and gaps in the evidence.
- Trace content is treated as untrusted. It is quoted into facts and never executed.
- A provider proposes hypotheses that cite fact ids. The server then decides each hypothesis's **evidence status**
  (`supported`, `unsupported`, `contradicted` or `inconclusive`) with fixed rules and caps its confidence.
- `supported` means the evidence matches the pattern of that cause; it is still a hypothesis. Only an experiment
  can mark it **validated**.
- A failed case without a usable trace gives `insufficient_evidence`, never a guessed cause.

### Providers

| Provider | Behaviour |
| --- | --- |
| `mock` (default) | Deterministic, offline, rule-based. No API key needed. |
| `anthropic` (experimental) | Uses Claude through the official SDK. Install with `pip install -e "backend[anthropic]"` and set `ANTHROPIC_API_KEY`. It has been tested only with a fake client, not against the live API. Installing it also pulls in `httpx2`, which changes the HTTP client used by the backend test suite. |

---

## Controlled experiments

An experiment runs one dataset version with a **baseline** and a **candidate** configuration of your agent and
compares the two.

1. **Create** the experiment in the console from a completed evaluation run. It reuses that run's dataset version
   and evaluators and records the difference between the two configurations.
2. **Run** it from your code. Traceforge calls your `task` function once per case for each configuration:

   ```python
   def task(case_input: dict, config: dict) -> dict:
       return {"answer": my_agent(case_input["question"], retriever=config["retriever"])}

   experiment = traceforge.run_experiment("<experiment id>", task)
   print(experiment["comparison"]["suggested_outcome"])
   ```

   Each case runs in its own trace, tagged `experiment` and `baseline` or `candidate`. If the run is interrupted,
   start it again: arms that are already scored are skipped.
3. **Compare** the results: pass rates and their difference, per-evaluator pass rates, retrieval recall, fixed and
   regressed cases, and latency, tokens and cost (compared only over cases measured in both arms).
4. **Validate a hypothesis** (optional): a hypothesis from a diagnosis is marked validated only if the baseline
   reproduced the failure and the candidate fixed it.
5. **Decide**: record an approve or reject decision with a written rationale. Decisions are append-only and audited.
   They do not change any agent, configuration or deployment.

`examples/sample_agent/experiment.py` shows a configurable agent (`retriever`: `keyword` or `stemmed`;
`context`: `first` or `query_overlap`).

---

## REST API reference

All endpoints are under `/api/v1`. Interactive docs: `http://127.0.0.1:8000/api/docs`; OpenAPI schema:
`/api/openapi.json`.

**Authentication:**
- The **console** uses a session cookie. State-changing requests also need the `X-CSRF-Token` header.
- **SDK and scripts** use a project API key: `Authorization: Bearer tf_<prefix>_<secret>`.

| Area | Endpoints | Auth |
| --- | --- | --- |
| Health | `GET /health` | None |
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Session |
| Sign-in providers | `GET /auth/providers`; `GET /auth/oauth/{provider}/start`, `GET /auth/oauth/{provider}/callback` (browser redirects) | None |
| Projects | `GET`, `POST /projects`; `GET`, `PATCH`, `DELETE /projects/{id}` | Session |
| API keys | `GET`, `POST /projects/{id}/api-keys`; `DELETE /projects/{id}/api-keys/{key_id}` | Session |
| Key check | `GET /api-keys/self` | API key |
| Ingestion | `POST /ingest/batch` (up to 100 traces and 1,000 spans) | API key |
| Traces | `GET /projects/{id}/traces`, `GET /projects/{id}/traces/{trace_id}` | Session |
| Datasets | `GET`, `POST /projects/{id}/datasets`; `GET`, `PATCH`, `DELETE .../datasets/{dataset_id}` | Session |
| Dataset cases | `GET`, `POST .../cases`; `PATCH`, `DELETE .../cases/{case_id}`; `POST .../import`; `GET .../export` | Session |
| Dataset versions | `GET`, `POST .../versions`; `GET .../versions/{version}` | Session |
| Evaluation runs | `GET`, `POST /projects/{id}/eval-runs`; `GET .../{run_id}`; `GET .../results`; `POST .../finish`, `.../abort`, `.../rerun` | Session |
| Evaluation (SDK) | `POST /sdk/eval-runs`; `GET /sdk/eval-runs/{id}`; `POST .../results`, `.../finish` | API key |
| Diagnoses | `POST /projects/{id}/eval-runs/{run_id}/diagnoses`; `POST .../diagnoses/{id}/retry`; `GET .../diagnoses`; `GET .../diagnoses/{id}`; `GET .../hypotheses/{hypothesis_id}` | Session |
| Experiments | `GET`, `POST /projects/{id}/experiments`; `GET .../{experiment_id}`; `GET .../cases`; `POST .../complete`, `.../abort`, `.../decisions`, `.../validate-hypothesis` | Session |
| Experiments (SDK) | `GET /sdk/experiments/{id}`; `POST .../start`, `.../complete` | API key |

The trace list supports the filters `status`, `q` (name or exact id), `tag`, `has_errors`, `since` and `until`, and
paginates with `limit` and `cursor`.

Every error uses the same JSON envelope with a `request_id`, which is also sent in the `X-Request-ID` header.

---

## Security model

| Area | How it works |
| --- | --- |
| **Passwords** | Hashed with Argon2id. Login takes the same time for unknown emails, so it does not reveal which emails exist. |
| **Emails** | Registration and login use the same normalised form, so the same address always matches. |
| **Sessions** | HttpOnly, SameSite=Lax cookie. Only a SHA-256 hash of the token is stored. Sessions expire and are revoked on logout. |
| **CSRF** | Every state-changing request with a session needs the `X-CSRF-Token` header. Login and registration accept only JSON and check the `Origin` header. |
| **Ownership** | Every route checks ownership. Another user's data returns 404, the same as missing data. A test calls every API route with another user's ids to prove this. |
| **API keys** | Format `tf_<prefix>_<secret>`. The full key is shown once; only an HMAC-SHA256 hash is stored. Keys do not expire: rotate them by creating a new key, switching over and revoking the old one. |
| **Audit log** | Records API key creation and revocation and project deletion. It never stores secrets. |
| **Logs and errors** | Logs are scrubbed of keys, tokens, cookies and passwords. Validation errors never echo submitted values. |
| **Response headers** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and a strict Content-Security-Policy for the console. |
| **Host check** | Only `127.0.0.1` and `localhost` are accepted as the `Host`, which blocks DNS-rebinding attacks. |
| **Untrusted content** | Trace, dataset and document content is stored and shown as data. It is never rendered as HTML or executed. |

### Failed-login rate limiting

After too many failed logins for the same IP and email (default 5), or from one IP across all emails (default 50),
within 15 minutes, login returns `429 too_many_attempts` with a `Retry-After` header. This applies even if the
password is then correct. There is no permanent account lockout.

The limiter is kept in memory in the server process. That is why only a **single server process** is supported:
several processes would each have their own budget, and a restart resets it. Because every local client connects
from `127.0.0.1`, all local clients share one per-IP budget.

---

## Recovery of interrupted work

Traceforge runs in one process with no job queue. When the server starts, and then every 60 seconds, it checks for
work left behind by a crash or restart and marks it as failed:

| Left behind | Considered interrupted when | What to do |
| --- | --- | --- |
| A diagnosis that never started | Older than 2 minutes | Click **Run again** |
| A diagnosis that stopped mid-way | Running longer than its time limit + timeout + grace (about 8 minutes) | Click **Run again** |
| An evaluation run stuck in scoring | No live scorer and older than 2 minutes | Re-run it |
| A running experiment | Past its deadline (1 hour by default) | Create it again |

Finished work is never changed. Experiments and evaluation runs waiting for **your** executor are not failed
automatically, because Traceforge cannot tell an idle executor from a dead one; abort them in the console if needed.
An interrupted `traceforge.run_experiment(...)` can be started again before its deadline.

---

## Backup and restore

All data (users, projects, API key hashes, traces, datasets, runs, diagnoses, experiments, decisions and the audit
log) is stored in PostgreSQL inside the `traceforge-postgres` container, in the Docker volume `traceforge_pgdata`.

| Database | Back it up? |
| --- | --- |
| `traceforge_dev` | **Yes**: this is your local data |
| `traceforge` | Yes, if you use it, and always before migrating it |
| `traceforge_test` | No: the test suite wipes it |

Backups contain sensitive data. Keep them in a private folder outside the repository and never commit them.

### Back up

```powershell
$db = "traceforge_dev"
$dir = "$HOME\traceforge-backups"; New-Item -ItemType Directory -Force $dir | Out-Null
$name = "$db-$(Get-Date -Format yyyyMMdd-HHmmss).dump"

docker exec traceforge-postgres sh -c "pg_dump -U `"`$POSTGRES_USER`" -d $db --format=custom --file=/tmp/$name"
docker cp "traceforge-postgres:/tmp/$name" "$dir\$name"
docker exec traceforge-postgres rm -f "/tmp/$name"
```

Write the dump inside the container and copy it out with `docker cp`, as shown above. Redirecting `pg_dump` output
with `>` in Windows PowerShell corrupts the file.

### Verify a backup

```powershell
$file = "$HOME\traceforge-backups\traceforge_dev-YYYYMMDD-HHMMSS.dump"
docker cp $file traceforge-postgres:/tmp/verify.dump
docker exec traceforge-postgres pg_restore --list /tmp/verify.dump > "$file.list.txt"
docker exec traceforge-postgres rm -f /tmp/verify.dump
(Select-String -Path "$file.list.txt" -Pattern " TABLE public ").Count     # expect 19
```

### Restore

Restore into a scratch database first, check it, then replace the original. Stop every Traceforge server before
replacing a database. The full step-by-step procedure, including checksums, is in
[docs/backup-and-restore.md](docs/backup-and-restore.md).

> Backups do not include `.env`. Keep a private copy of your secrets: restoring with a different
> `TRACEFORGE_API_KEY_PEPPER` invalidates every API key.

---

## Running the tests

| Suite | Command | Notes |
| --- | --- | --- |
| Backend | `cd backend; ..\.venv\Scripts\python -m pytest` | Integration tests against real PostgreSQL. Uses only `traceforge_test`. |
| SDK | `$env:PYTHONPATH="sdk\python\src"; .venv\Scripts\python -m pytest sdk\python -q` | Tested on Python 3.10 and 3.11 |
| Frontend unit | `cd frontend; npm test` | Vitest + jsdom |
| Type check | `cd frontend; npm run typecheck` | |
| Build | `cd frontend; npm run build` | Output in `frontend/dist` |
| End-to-end | `cd frontend; npm run e2e` | Real Edge browser against the real stack |
| Release check | `cd frontend; npm run e2e:release` | Builds the console and tests it served by `python -m app.serve` |

The end-to-end suite starts its own backend on `traceforge_test`, registers a user, creates a project and API key in
the UI, runs the sample agent and checks the results. Screenshots are saved to `frontend/e2e/screenshots/`. Run it
from PowerShell; it uses the installed Microsoft Edge, so no browser download is needed.

The backend test suite refuses to start unless `TEST_DATABASE_URL` names exactly `traceforge_test`, and it never
connects to the main database.

---

## Supported deployment

Traceforge 0.1.0 is a **single-user, single-process, localhost-only** application.

| Supported | Not supported |
| --- | --- |
| One `python -m app.serve` process on the same machine as the browser | Several API processes or workers sharing one database |
| Binding to `127.0.0.1`, `localhost` or `::1` | Binding to `0.0.0.0` or a LAN address, port forwarding, tunnels |
| Plain HTTP on loopback | Exposing the server to a network, with or without HTTPS |
| PostgreSQL 16 in the bundled Docker container | Shared or remote database servers |

Why: there is no TLS, registration is open to anyone who can reach the server, and the login rate limiter and the
recovery sweep live inside the server process.

If you start the server with Uvicorn directly instead of `app.serve`, you **must** pass `--no-proxy-headers`:

```powershell
python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

Without that flag, any local process could fake its IP address and bypass the login rate limiter.

---

## Known limitations

- **Localhost only**: see [Supported deployment](#supported-deployment).
- Experiments compare exactly two configurations, without repeated trials or statistical significance.
- Experiments replay through a task function you provide; Traceforge cannot reconfigure external agents itself.
- Diagnosis checks grounding with word overlap, so paraphrased use of a document can be missed. It needs document
  content on retrieval spans.
- The Anthropic diagnosis provider is experimental and has not been run against the live API.
- Cost and token comparisons rely on the usage your agent reports on LLM spans.
- There is no password reset or account management beyond sign-in, and API keys do not expire.
- Registration reveals whether an email already has an account (it returns 409).
- There is no data retention policy or automated backup: data stays until its project or dataset is deleted.
- Scoring and diagnosis run inside the server process, which is fine locally but not for large workloads.

More detail: [docs/release-checklist.md](docs/release-checklist.md), [backend/README.md](backend/README.md),
[frontend/README.md](frontend/README.md), [sdk/python/README.md](sdk/python/README.md) and
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

---

## Author

**Hemanth Kumar**

- Email: [hk744610@gmail.com](mailto:hk744610@gmail.com)
- LinkedIn: [linkedin.com/in/hemanth131104](https://www.linkedin.com/in/hemanth131104/)
- GitHub: [github.com/hemanth210205](https://github.com/hemanth210205)

Found a bug or have an idea? Open an issue or a pull request, or get in touch.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
