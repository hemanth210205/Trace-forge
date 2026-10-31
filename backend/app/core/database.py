"""Which PostgreSQL database a process may use, decided before anything connects.

``DATABASE_URL`` names the *main* database (``traceforge``). It is the reference for the other names and is only
ever connected to when explicitly selected:

========  ======================  ==============================  =========================================
choice    URL variable            required database name          how it is selected
========  ======================  ==============================  =========================================
``dev``   ``DEV_DATABASE_URL``    ``<main>_dev`` (traceforge_dev)  the default for every server start
``test``  ``TEST_DATABASE_URL``   ``<main>_test``                  ``--database test`` / the test suite
``main``  ``DATABASE_URL``        ``<main>``                       ``--database main`` only (never a default)
========  ======================  ==============================  =========================================

The choice comes from the command line (``python -m app.serve --database ...``) or the *process* environment
variable ``TRACEFORGE_DATABASE``. It is deliberately not read from ``.env``, so a line in a file can never
silently switch a server to the main database. Error messages name databases, never URLs or credentials.
"""

import os
from typing import Literal, cast

from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings

DatabaseChoice = Literal["dev", "test", "main"]
CHOICES: tuple[DatabaseChoice, ...] = ("dev", "test", "main")
CHOICE_ENV = "TRACEFORGE_DATABASE"
DEFAULT_CHOICE: DatabaseChoice = "dev"


class UnsafeDatabase(SystemExit):
    """Raised (with a message, exit code 2 when uncaught at the top level) instead of connecting."""


def database_name(url: str) -> str | None:
    return make_url(url).database


def _resolve(main_url: str, url: str | None, suffix: str, variable: str) -> str:
    if not url:
        raise UnsafeDatabase(f"Refusing to start: {variable} is not set.")
    main_db, target_db = database_name(main_url), database_name(url)
    expected = f"{main_db}_{suffix}"
    if not main_db or target_db != expected or target_db == main_db:
        raise UnsafeDatabase(f"Refusing to start: {variable} must name {expected!r}, not {target_db!r}.")
    return url


def resolve_test_database_url(main_url: str, test_url: str | None) -> str:
    """Only ``<DATABASE_URL database>_test``. Mirrors the test-suite guard in tests/conftest.py."""
    return _resolve(main_url, test_url, "test", "TEST_DATABASE_URL")


def resolve_dev_database_url(main_url: str, dev_url: str | None) -> str:
    """Only ``<DATABASE_URL database>_dev`` (traceforge_dev); never the main or the test database."""
    return _resolve(main_url, dev_url, "dev", "DEV_DATABASE_URL")


def parse_choice(value: str | None) -> DatabaseChoice:
    choice = (value or DEFAULT_CHOICE).strip().lower()
    if choice not in CHOICES:
        raise UnsafeDatabase(f"Refusing to start: {CHOICE_ENV} must be one of {', '.join(CHOICES)}, not {value!r}.")
    return cast(DatabaseChoice, choice)


def choice_from_environment() -> DatabaseChoice:
    return parse_choice(os.environ.get(CHOICE_ENV))


def resolve_database_url(settings: Settings, choice: DatabaseChoice) -> str:
    main_url = settings.database_url.get_secret_value()
    if choice == "dev":
        return resolve_dev_database_url(main_url, settings.dev_database_url.get_secret_value() if settings.dev_database_url else None)
    if choice == "test":
        return resolve_test_database_url(main_url, settings.test_database_url.get_secret_value() if settings.test_database_url else None)
    if not database_name(main_url):
        raise UnsafeDatabase("Refusing to start: DATABASE_URL names no database.")
    return main_url


def settings_for(settings: Settings, choice: DatabaseChoice) -> Settings:
    """``settings`` with ``database_url`` replaced by the selected, guarded database."""
    return settings.model_copy(update={"database_url": SecretStr(resolve_database_url(settings, choice))})


def check_migration_target(target_url: str, main_url: str, *, confirm: str | None = None) -> str:
    """The name guard for every migration or schema operation. Returns the target database name.

    ``<main>_dev`` and ``<main>_test`` are allowed. The main database is only allowed when ``confirm`` repeats its
    exact name (``alembic -x database=main -x confirm=traceforge upgrade head``). Anything else is refused.
    """
    main_db, target_db = database_name(main_url), database_name(target_url)
    if not main_db or not target_db:
        raise UnsafeDatabase("Refusing to migrate: the database name is missing.")
    if target_db in (f"{main_db}_dev", f"{main_db}_test"):
        return target_db
    if target_db == main_db:
        if confirm == main_db:
            return target_db
        raise UnsafeDatabase(
            f"Refusing to migrate the main database {main_db!r} without confirmation. Back it up first "
            f"(docs/backup-and-restore.md), then run: alembic -x database=main -x confirm={main_db} upgrade head"
        )
    raise UnsafeDatabase(f"Refusing to migrate {target_db!r}: only {main_db}_dev, {main_db}_test or a confirmed "
                         f"{main_db!r} may be migrated.")
