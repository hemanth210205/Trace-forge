# Traceforge console

React + TypeScript + Vite + Tailwind. The console always talks to the API on its own origin (`/api/v1`), so the
HttpOnly session cookie and CSRF header work without cross-site cookies.

**Using Traceforge** (one server): `npm run build` once, then `python -m app.serve` in `backend/` serves the API and
this build at http://127.0.0.1:8000 (see the repository README). Rebuild after changing the console.

**Developing the console** (two terminals, hot reload):

```powershell
# terminal 1
cd backend
..\.venv\Scripts\python -m app.serve --reload          # API on 127.0.0.1:8000, database traceforge_dev
# terminal 2
cd frontend
npm install
npm run dev            # http://localhost:5173, proxies /api to http://127.0.0.1:8000 (TRACEFORGE_API_URL to change)
```

```powershell
npm test               # unit + page tests (Vitest, jsdom; fetch is stubbed at the network boundary)
npm run typecheck
npm run build          # production build in dist/ (served by python -m app.serve)
npm run e2e            # real browser (system Microsoft Edge) against the real stack, see below
npm run e2e:release    # builds, starts `python -m app.serve --database test --port 8001`, fresh browser session
```

Run the end-to-end suites from PowerShell (the system Edge is used; no browser download).

`npm run e2e` starts its own backend on the disposable `traceforge_test` (`python -m app.dev_test_server`)
and Vite, registers a user, creates a project and an API key in the
UI, runs `examples/sample_agent/agent.py` with the key, and inspects the resulting traces. Screenshots go to
`e2e/screenshots/`. Running the backend test suite clears the data the dev server stored.

## Screens

- **Projects**: list, create (then straight to API keys).
- **API keys**: create (secret shown once), list with last use, revoke.
- **Traces**: newest first; search by name or exact id, status, time range, tag, "spans with errors";
  keyset pagination ("Load more"); filters live in the URL.
- **Trace detail**: summary (status, duration, spans, errors, tokens, cost, user/session), span timeline
  (hierarchy with collapse, waterfall bars on a shared time axis, kind and status markers; spans whose parent
  never arrived are shown at top level marked *orphan*; unfinished spans drawn as *open*), and a details panel
  with error and stack trace, model/tokens/cost (only when reported), tool name and arguments, retrieved
  documents with source and score, input, output, attributes and metadata. Payloads are rendered as text only.
