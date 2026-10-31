"""Supported way to run Traceforge: ``python -m app.serve`` (from ``backend/``).

    python -m app.serve                     # traceforge_dev (the default; migrated to head if needed)
    python -m app.serve --database test     # traceforge_test (disposable: the test suite truncates it)
    python -m app.serve --database main     # the main database: explicit only, never migrated here

The database is chosen and name-checked (``app/core/database.py``) before anything connects; the main database is
never used unless ``--database main`` is given, and then only if it is already at the code's schema revision. The
built console (``frontend/dist``, from ``npm run build``) is served at ``/`` when present. Credentials are never
printed.

Proxy-header handling is hard-coded off. Uvicorn otherwise rewrites the client address from
``X-Forwarded-For`` (and the scheme from ``X-Forwarded-Proto``) whenever the peer is in
``forwarded_allow_ips``, which defaults to 127.0.0.1 and ::1. In this local-only deployment every client
connects over loopback, so any local process could pick its own client IP, and with it a fresh login
rate-limit budget on every request. The options below are passed directly to ``uvicorn.run``; no
command-line flag or environment variable (``FORWARDED_ALLOW_IPS``, ``UVICORN_*``) can re-enable them.
``X-Real-IP`` is not interpreted by uvicorn or by the application.
"""

import argparse
import os
import sys
from typing import Any

import uvicorn

# Applied to every server started by this module; tests/test_server_runtime.py serves the app with them.
SERVER_OPTIONS: dict[str, Any] = {"proxy_headers": False, "forwarded_allow_ips": []}
VITE_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # the Vite dev server (npm run dev)
# Traceforge is a localhost-only product (plain HTTP, in-process limiter and recovery): no other bind address.
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def prepare(choice: str) -> str:
    """Guards, migrates (dev/test only) or verifies (main) the database; returns a one-line description."""
    from sqlalchemy import create_engine

    from app.core.config import get_settings
    from app.core.database import CHOICE_ENV, UnsafeDatabase, database_name, parse_choice, resolve_database_url
    from app.db.schema import SchemaOutOfDate, ensure_current, migrate_to_head
    from app.db.session import CONNECT_ARGS

    settings = get_settings()
    try:
        selected = parse_choice(choice)
        url = resolve_database_url(settings, selected)
    except UnsafeDatabase as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(2) from None
    name = database_name(url)
    if selected == "main":
        engine = create_engine(url, connect_args=CONNECT_ARGS)
        try:
            revision = ensure_current(engine, name)
        except SchemaOutOfDate as exc:
            print(f"Refusing to start: {exc}", file=sys.stderr)
            raise SystemExit(2) from None
        finally:
            engine.dispose()
    else:
        revision = migrate_to_head(url, settings.database_url.get_secret_value())
        # The Vite dev server is a trusted origin for the development and test databases.
        origins = [o for o in settings.cors_origin_list if o not in VITE_ORIGINS] + list(VITE_ORIGINS)
        os.environ["TRACEFORGE_CORS_ORIGINS"] = ",".join(origins)
    # Process-local: inherited by the --reload worker; .env is not modified.
    os.environ[CHOICE_ENV] = selected
    get_settings.cache_clear()
    lifetime = {"dev": "persistent development data", "test": "disposable (the test suite truncates it)",
                "main": "the main database"}[selected]
    return f"database {name!r} at revision {revision}: {lifetime}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.serve", description="Run Traceforge (API and console).")
    parser.add_argument("--database", choices=("dev", "test", "main"), default="dev",
                        help="dev: DEV_DATABASE_URL (default); test: TEST_DATABASE_URL; main: DATABASE_URL (explicit only)")
    parser.add_argument("--host", default="127.0.0.1", help="Loopback bind address: 127.0.0.1 (default), localhost or ::1.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Restart on code changes (development).")
    args = parser.parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        print(f"Refusing to start: Traceforge only serves on loopback ({', '.join(LOOPBACK_HOSTS)}), not {args.host!r}. "
              "Networked deployment is not supported.", file=sys.stderr)
        raise SystemExit(2)
    description = prepare(args.database)

    from app.core.config import get_settings

    dist = get_settings().frontend_dist
    console = f"console at http://{args.host}:{args.port}/" if dist and os.path.isfile(os.path.join(dist, "index.html")) \
        else "console not built (run `npm run build` in frontend/, or use `npm run dev`)"
    print(f"Traceforge on {description}; {console}", flush=True)
    uvicorn.run(
        "app.main:create_app", factory=True, host=args.host, port=args.port, reload=args.reload, **SERVER_OPTIONS
    )


if __name__ == "__main__":
    main()
