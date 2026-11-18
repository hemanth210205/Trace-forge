# Local release checklist

Traceforge 0.1.0 is a **single-user, single-process, localhost-only** application. This page states what that
means, what is and is not supported, and what to check before calling a build releasable.

## Supported deployment

| Supported | Not supported |
| --- | --- |
| One `python -m app.serve` process on the same machine as the browser | Several API processes or workers against one database |
| Binding to loopback (`127.0.0.1`, `localhost`, `::1`) | Binding to `0.0.0.0` or a LAN address (`app.serve` refuses), port forwarding, tunnels |
| Plain HTTP on loopback | Exposing the server to a network, with or without HTTPS |
| Requests whose `Host` is `127.0.0.1` or `localhost` | Any other host name (400), reverse proxies (`X-Forwarded-*` is ignored) |
| PostgreSQL 16 in the bundled Docker container on `127.0.0.1:5433` | Shared or remote database servers |

Why multi-process and networked deployment are unsupported (not merely untested):

- The failed-login rate limiter is in memory per process; several processes multiply the allowance.
- The recovery sweep and diagnosis background tasks run in the server process; with several processes they would
  still be correct at the row level (locks, `SKIP LOCKED`), but this has not been designed or tested.
- There is no TLS, so session cookies and API keys would cross the network in clear text.
- Registration is open to anyone who can reach the server, and the 409 for a taken email reveals accounts.

## Security assumptions (localhost)

- **Everyone who can reach the port is local.** Other local users on the same machine can reach `127.0.0.1:8000`
  too; on a shared machine anyone there can register an account (they cannot see other accounts' projects).
- **HTTP only, cookies not `Secure`.** `TRACEFORGE_COOKIE_SECURE=false` is required for plain HTTP on loopback.
  The session cookie is HttpOnly and SameSite=Lax; state-changing requests also need the CSRF header; login and
  registration check `Origin` (own origin or `TRACEFORGE_CORS_ORIGINS`). Setting `COOKIE_SECURE=true` without
  HTTPS makes sign-in impossible; there is no supported HTTPS setup.
- **Browser-based attacks**: foreign pages cannot use the session (SameSite + CSRF + Origin), cannot frame the
  console (`X-Frame-Options: DENY`, `frame-ancestors 'none'`), and DNS-rebinding pages are rejected by the Host
  check. The console runs under a Content-Security-Policy that allows only its own scripts and API.
- **Secrets**: `.env` holds the database password, `TRACEFORGE_SECRET_KEY` and `TRACEFORGE_API_KEY_PEPPER`.
  Never commit it. API keys do not expire; revoke unused ones. Logs are scrubbed of keys, tokens and passwords.
- **Untrusted content** recorded by agents (trace payloads, documents) is displayed as text and never executed.
  SDK redaction is pattern-based and cannot recognise every secret or personal datum: decide what your agents
  record (`capture_inputs` / `capture_outputs`).

## Experimental: Anthropic diagnosis provider

The default diagnosis provider is the deterministic offline `mock`. `TRACEFORGE_DIAGNOSIS_PROVIDER=anthropic`
(optional `anthropic` package, `ANTHROPIC_API_KEY`) is **experimental**: it is tested only against a fake SDK client
and has never been run against the live API. Installing the `anthropic` package also pulls in `httpx2`, which
changes the HTTP client used by the backend test suite. Treat its output as unverified until it has been
exercised against the live API.

## Clean installation (Windows, PowerShell)

Prerequisites: Docker Desktop, Python 3.11 (backend; the SDK supports 3.10+), Node.js 20+ (tested with 24) and
npm, Microsoft Edge (only for the end-to-end tests).

```powershell
git clone <repository> Traceforge; cd Traceforge
Copy-Item .env.example .env
# Edit .env: set POSTGRES_PASSWORD (and the same password in the three *_DATABASE_URL lines),
# TRACEFORGE_SECRET_KEY and TRACEFORGE_API_KEY_PEPPER. Generate each secret with:
#   py -3.11 -c "import secrets; print(secrets.token_urlsafe(48))"

docker compose up -d postgres           # first start creates traceforge, traceforge_test and traceforge_dev

py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e "backend[dev]" -c backend\constraints.txt
.venv\Scripts\python -m pip install -e sdk\python

cd frontend; npm ci; npm run build; cd ..
```

## Startup

```powershell
cd backend
..\.venv\Scripts\python -m app.serve
```

Open http://127.0.0.1:8000 and create an account. The first start migrates `traceforge_dev` to the current
schema. The startup line names the database and revision (never credentials). Stop with Ctrl+C.

- Use `--database test` for the disposable test database. Use `--database main` only for the main database, and
  only after it has been backed up and migrated (below).
- Console development with hot reload: `python -m app.serve --reload` plus `npm run dev` (http://localhost:5173).
- Send telemetry: create an API key in the console, then `examples/sample_agent/agent.py` (see `sdk/python/README.md`).

## The main database

`traceforge` is never used or migrated implicitly. It was last verified at schema revision **0001** (milestone M7;
it has deliberately not been connected to since); the code needs **0006**, so `python -m app.serve --database main`
refuses to start until it is migrated. To adopt it:

1. Stop every Traceforge server.
2. Back it up and verify the backup (`docs/backup-and-restore.md`, with `$db = "traceforge"`).
3. `cd backend; ..\.venv\Scripts\python -m alembic -x database=main -x confirm=traceforge upgrade head`
4. `..\.venv\Scripts\python -m app.serve --database main`

Not done for this release, by decision.

## Release verification

Run from PowerShell in the repository root unless noted; every item must pass.

- [ ] `cd backend; ..\.venv\Scripts\python -m pytest` (uses only `traceforge_test`)
- [ ] `$env:PYTHONPATH="sdk\python\src"; .venv\Scripts\python -m pytest sdk\python -q` (and with Python 3.10)
- [ ] `cd frontend; npm test; npm run typecheck; npm run build`
- [ ] `cd frontend; npm run e2e` (development stack) and `npm run e2e:release` (built console served by
      `python -m app.serve`, fresh browser session)
- [ ] `git status --ignored` shows `.env`, `.venv`, `node_modules`, `frontend/dist`, test outputs and caches as
      ignored, and no dumps or secrets staged
- [ ] A backup of `traceforge_dev` was taken and verified (`docs/backup-and-restore.md`)

## Known limitations

- Interrupted work is marked failed, not resumed (details in `docs/backup-and-restore.md`); a crashed diagnosis
  shows `running` for up to about 8 minutes first.
- No data retention policy: data stays until its project or dataset is deleted. No automated backups.
- Diagnosis attribution is lexical; experiments compare exactly two arms without statistics.
- No password reset or account management beyond sign-in; API keys do not expire.
