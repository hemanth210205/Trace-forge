# Traceforge implementation status

_Last updated: 2026-10-31 (local-release preparation)_

## Completed milestones

| Milestone | Status | Acceptance |
| --- | --- | --- |
| M2 Backend foundation | Done, approved | Auth, sessions, CSRF, projects, API keys, audit log, login rate limiting |
| M3 Python SDK and trace ingestion | Done, approved | `backend/tests/test_sdk_end_to_end.py` |
| M4 Trace Explorer | Done, approved | `frontend/e2e/traceExplorer.spec.ts` (real Edge browser) |
| M5 Datasets and deterministic evaluation | Done, approved | `frontend/e2e/evaluation.spec.ts`, `backend/tests/test_sdk_evaluation_end_to_end.py` |
| M6 Failure diagnosis | Done, approved | `frontend/e2e/diagnosis.spec.ts` (real browser), `backend/tests/test_sdk_diagnosis_end_to_end.py` |
| M7 Controlled experiments | Done, approved | `frontend/e2e/experiment.spec.ts` (real browser), `backend/tests/test_sdk_experiment_end_to_end.py` |
| M8 Reliability, security and product completion | Done, approved | `backend/tests/test_recovery.py`, `test_interrupted_execution.py`, `test_isolation.py`, `frontend/e2e/reliability.spec.ts` |

| Local-release preparation | Done, **awaiting review**; no commit yet | `frontend/e2e-release/startup.spec.ts`, `backend/tests/test_startup_safety.py` |

## Current milestone

None in progress. No further feature milestone is planned. The first Git commit awaits approval.

## Local-release preparation

- **Safe database startup** (`backend/app/core/database.py`): `python -m app.serve` uses `traceforge_dev` by default
  (migrated to head on start); `--database test` and `--database main` are explicit; the main database is never a
  default, never migrated by the server, and refused unless already at head. `create_app()` without settings reads
  `TRACEFORGE_DATABASE` from the process environment only (default `dev`). Every app start refuses a schema that is
  not at head. `alembic` targets `traceforge_dev` by default; the main database needs
  `-x database=main -x confirm=traceforge`; any other name is refused before connecting.
- **One-command local serving**: `python -m app.serve` serves the API and the built console (`frontend/dist`) on
  http://127.0.0.1:8000 with SPA fallback, a strict Content-Security-Policy and no effect on `/api` routes. Loopback
  bind only; `Host` limited to `127.0.0.1`/`localhost` (blocks DNS rebinding), which makes trusting the console's
  own origin for login safe. `npm run dev` remains for console development.
- **Fresh installs**: the Postgres init script now also creates `traceforge_dev` (new volumes only);
  `backend/constraints.txt` pins the tested dependency set.
- **Docs**: `README.md`, `docs/release-checklist.md` (localhost-only assumptions, unsupported deployments, cookie/HTTP
  limits, experimental Anthropic provider, clean install, verification), `docs/backup-and-restore.md` (tested backup
  and verification; restore documented, not executed).

## M8 changes

- **Recovery of interrupted work** (`backend/app/services/recovery.py`): a sweep at startup and every 60 s fails
  diagnoses left `pending`/`running` by a crash, evaluation runs stuck in `scoring` (live scorers are skipped via
  their row lock) and running experiments past their deadline (their arm runs stop accepting results). `SKIP
  LOCKED`, same state machines and triggers, terminal records untouched; a live worker that loses the race writes
  nothing. A killed `run_experiment` process can be resumed before the deadline. No schema change.
- **Isolation**: a sweep test calls every route in the OpenAPI schema with another user's real ids (foreign
  project, and own project with foreign child ids, and foreign API key) — all 404, nothing leaked or changed.
- **Errors**: 413 for streamed oversized bodies (was a misleading 400); 413 and 500 now carry CORS headers, the
  request id and `X-Request-ID`; 500 logged once; values PostgreSQL cannot store (NaN, Infinity, NUL) are a 422
  instead of a 500 (which made SDKs retry and then drop whole batches).
- **Security**: one canonical email form for registration and login (Unicode-normalized addresses could register
  but never log in); `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`
  on every response.
- **SDK**: NaN/Infinity and NUL made safe before sending; malformed LLM usage no longer drops the span; tested on
  Python 3.10 and 3.11; the built wheel is pure Python with no runtime dependencies.
- **Console**: unknown or foreign project ids show "Project not found" without project tabs or pages (was: tabs and
  page errors); failed dataset/case deletion and failed diagnosis re-runs are shown (were silent) and their buttons
  are disabled while in flight; the dataset delete warning names everything that is deleted; an experiment past its
  deadline says so; tabs scroll and buttons keep one line at phone width (375 px, no horizontal page scroll).
- **Deletion**: deleting a dataset or project removes the whole validated/decided lifecycle (append-only decisions
  and frozen hypotheses included) — tested.

## Working functionality (M7)

- **Experiments**: created from a completed evaluation run (exact dataset version, same evaluators); baseline and
  candidate configurations with a recorded diff; two arm evaluation runs; `pending → running → completed | failed`
  enforced by API locks and DB triggers (arm integrity, immutable definition, frozen when finished); case and time
  bounds; failed arms fail the experiment.
- **Execution**: `traceforge.run_experiment(id, task)` calls `task(case_input, config)` per case per arm (traces tagged
  with experiment and arm; resumable). The sample agent is configurable (`retriever`, `context`).
- **Comparison**: pass rates (only when both arms completed), evaluator pass rates, retrieval recall, fixes,
  regressions, evaluator-level changes, and latency/tokens/cost over cases measured in both arms (else "not
  measured"); per-case baseline vs candidate with trace links; suggested outcome clearly not a decision.
- **Hypotheses and promotion**: an experiment can test an M6 hypothesis (its recommendation shown as context, not
  evidence); validation only when the baseline reproduced the failure and the candidate fixed it; append-only,
  audited human approve/reject decisions with rationale; nothing is applied to any agent automatically.
- **Demonstrated**: keyword → stemmed retriever fixes `shipping-a100` (50% → 75%, no regressions,
  `warranty-accidental-damage` still failing); the shipping retrieval hypothesis becomes validated by the experiment.

## Working functionality (M6)

- **Diagnosis workflow** (`backend/app/diagnosis/`): explicit typed state machine with 11 steps (load run → failed
  cases → traces → execution analysis → output comparison → hypotheses → evidence assessment → confidence →
  missing evidence → experiments → persist), validated transitions, step/time/case/retry bounds. Runs as a
  background task; status `pending → running → completed | failed`.
- **Evidence**: read through allowlisted functions in a `READ ONLY` transaction, project-scoped and bounded; facts
  computed deterministically; untrusted trace content quoted, never executed.
- **Hypotheses**: eight categories; each with explanation, supporting and contradicting facts, trace and span ids
  derived only from cited facts, server-computed evidence status (supported / unsupported / contradicted /
  inconclusive), capped confidence, uncertainty, missing evidence and a recommended experiment. Validation status
  stays `unvalidated` (only an experiment can validate; enforced in the database).
- **Providers**: deterministic `mock` (default, offline) and optional `anthropic` (official SDK, structured output,
  typed error mapping); provider-agnostic workflow; credentials server-side and scrubbed from errors and logs.
- **Persistence and API**: start, retry (history kept), list, detail, single hypothesis; finished diagnoses immutable.
- **Console**: "Diagnose failures" on scored runs, diagnosis history per run and project, diagnosis page that
  separates observed facts, hypotheses and validated causes, with links from facts and hypotheses to Trace
  Explorer spans.
- **Demonstrated on the sample agent**: warranty case → context selection failure supported (grounding contradicted);
  shipping case → retrieval failure supported for the source check only, explicitly not an incorrect answer; run
  without traces → insufficient evidence for every case.

## Database state

No migration was added in M8 or the release preparation. dev and test re-verified at 0005 (19 tables, 10 triggers each); the real `traceforge`
database was not connected to by M8 (a read-only check of it was declined by the permission policy, so its
revision was last verified at M7: 0001, 6 tables).


| Database | Revision | Tables | Purpose |
| --- | --- | --- | --- |
| `traceforge` (real) | **0001** | 6 | Untouched |
| `traceforge_dev` | **0005** (head) | 19 | Persistent local development |
| `traceforge_test` | **0005** (head) | 19 | Disposable; automated tests |

## Dependencies

No packages were installed for M6, M7 or M8. `httpx` stays at 0.28.1; `httpx2`, LangGraph, LangChain and `anthropic` are not
installed. LangGraph is not used because every current release requires `httpx2` (via `langsmith`). `anthropic` is
declared as an optional extra (`backend[anthropic]`) for the real provider.

## Tests

- Backend: **345 passed** (`cd backend; ..\.venv\Scripts\python -m pytest`), PostgreSQL `traceforge_test` only.
- SDK: **21 passed** on Python 3.11 and on Python 3.10.
- Frontend: **37 passed** (`npm test`), typecheck clean.
- End-to-end (real Edge browser, real stack): **5 passed** (`npm run e2e`) and **1 passed** (`npm run e2e:release`:
  built console served by `python -m app.serve`), from PowerShell.
- 1 accepted warning: Starlette `httpx` TestClient deprecation.

## Known limitations

- Experiments replay through a task function you provide; arbitrary external agents cannot be reconfigured or
  replayed by Traceforge. Configuration keys are opaque to the server (it records and diffs them).
- One experiment compares exactly two arms; repeated trials and statistical significance are not computed.
- Cost and token comparisons rely on usage reported on LLM spans; the sample agent's cost uses an illustrative rate.

- The real Anthropic provider has not been exercised against the live API (no key in this environment and the SDK
  is not installed); its wrapper is tested with a fake SDK client.
- Grounding and context attribution use lexical overlap; paraphrased use of a document can be missed (stated in
  each hypothesis's uncertainty). They need document content on retrieval spans.
- Diagnoses run in-process as background tasks. After a crash they are failed by the recovery sweep only once
  their time budget (time limit + provider timeout + grace, about 8 minutes by default) has passed; until then they
  still show `running`. Experiments waiting for an executor and SDK-driven evaluation runs have no heartbeat and
  are never failed automatically (an idle executor cannot be told from a dead one); abort them in the console.
- Single-process only: the login limiter and the recovery sweeper are in-process. Running several API workers
  against one database is not supported.
- Registration reveals whether an email already has an account (409); inherent to open self-registration.
- Redaction is pattern-based and cannot catch every secret or personal datum in traces.
- No data retention policy: traces, runs, diagnoses and experiments are kept until their project or dataset is
  deleted.
- `tool_selection_failure` can never be marked supported from traces alone; it needs an experiment.
- Scoring and diagnosis are synchronous per request/task (fine locally, not for large workloads).
- Nothing is committed to git yet.

## Remaining work

The main `traceforge` database stays at 0001 by decision (see `docs/release-checklist.md` to adopt it).

## Next action

Review the release preparation; approve the first Git commit.
