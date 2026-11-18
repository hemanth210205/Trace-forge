"""Schema revision checks and guarded migration (the only code that upgrades a database outside tests)."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine

from app.core.database import check_migration_target
from app.db.session import CONNECT_ARGS

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str, *, confirm: str | None = None) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    # Read by alembic/env.py's own guard, which checks the target again before connecting.
    config.attributes["confirm"] = confirm
    return config


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config("postgresql://unused/unused")).get_current_head()


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


class SchemaOutOfDate(RuntimeError):
    pass


def ensure_current(engine: Engine, database: str | None) -> str:
    """Raises SchemaOutOfDate unless the database is at the code's head revision (nothing is changed)."""
    current, head = current_revision(engine), head_revision()
    if current != head:
        raise SchemaOutOfDate(
            f"Database {database!r} is at revision {current or 'none'}, but this version of Traceforge needs {head}. "
            "Nothing was changed. See docs/release-checklist.md for how to migrate it safely."
        )
    return current or "base"


def migrate_to_head(url: str, main_url: str, *, confirm: str | None = None) -> str:
    """Upgrades a dev or test database to head (the name guard refuses every other database). The main database is
    accepted only with ``confirm`` equal to its exact name. Only ever upgrades: a database at a revision this code
    does not know (newer code was deployed before) makes Alembic fail instead of guessing."""
    check_migration_target(url, main_url, confirm=confirm)
    engine = create_engine(url, connect_args=CONNECT_ARGS)
    try:
        current = current_revision(engine)
    finally:
        engine.dispose()
    head = head_revision()
    if current != head:
        command.upgrade(alembic_config(url, confirm=confirm), "head")
    return head or "base"
