"""Development server on a non-main database (kept for existing scripts; ``app.serve`` does the work).

    python -m app.dev_test_server                    # traceforge_test (disposable; the test suite truncates it)
    python -m app.dev_test_server --database dev     # traceforge_dev  (persistent local development data)

Equivalent to ``python -m app.serve --database test|dev``: the database is name-checked before anything connects
(exit code 2 otherwise), migrated to head if needed, the Vite origins are trusted, and credentials are never
printed. It can never select the main database.
"""

import argparse

from app.core.database import UnsafeDatabase, resolve_dev_database_url, resolve_test_database_url

__all__ = ["UnsafeDatabase", "main", "resolve_dev_database_url", "resolve_test_database_url"]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.dev_test_server", description=__doc__.splitlines()[0])
    parser.add_argument("--database", choices=("test", "dev"), default="test",
                        help="test: TEST_DATABASE_URL (disposable, default); dev: DEV_DATABASE_URL (persistent)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    from app.serve import main as serve

    serve(["--database", args.database, "--port", str(args.port), *(["--reload"] if args.reload else [])])


if __name__ == "__main__":
    main()
