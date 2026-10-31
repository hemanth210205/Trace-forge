# Traceforge

Local-first observability, evaluation, failure diagnosis and controlled experiments for AI agents.

- **Trace** agents with the Python SDK (`sdk/python`) and inspect every LLM call, tool call and retrieval.
- **Evaluate** them on immutable dataset versions with deterministic evaluators.
- **Diagnose** failed cases into evidence-backed, explicitly unvalidated hypotheses.
- **Experiment**: replay a baseline and a candidate configuration, compare them, validate hypotheses and record a
  human promotion decision. Traceforge never changes or deploys your agent.

Single user, single process, **localhost only**: read `docs/release-checklist.md` before using it.

## Quick start (Windows, PowerShell)

```powershell
Copy-Item .env.example .env              # then set the password and the two secrets (see the checklist)
docker compose up -d postgres
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -e "backend[dev]" -c backend\constraints.txt
.venv\Scripts\python -m pip install -e sdk\python
cd frontend; npm ci; npm run build; cd ..
cd backend; ..\.venv\Scripts\python -m app.serve
```

Open http://127.0.0.1:8000. The server uses the `traceforge_dev` database by default and never selects the main
`traceforge` database unless started with `--database main`.

## Documentation

| Topic | Where |
| --- | --- |
| Installation, startup, security assumptions, supported deployment | `docs/release-checklist.md` |
| Backups, restore, interrupted work | `docs/backup-and-restore.md` |
| API, databases, migrations, security model | `backend/README.md` |
| Console development and tests | `frontend/README.md` |
| Instrumenting an agent, evaluations, experiments | `sdk/python/README.md`, `examples/sample_agent/` |
| Milestones and current status | `IMPLEMENTATION_STATUS.md` |
