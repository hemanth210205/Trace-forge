from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

import app.models  # noqa: F401  (registers models on Base.metadata)
from app.db.base import Base
from app.db.session import CONNECT_ARGS

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """The guarded target. Runs before anything connects, for the CLI and for programmatic callers alike.

    CLI: ``alembic upgrade head`` targets traceforge_dev; ``-x database=test`` targets traceforge_test; the main
    database needs ``-x database=main -x confirm=<its name>``. Programmatic callers set ``sqlalchemy.url`` and get
    the same name check (only ``<main>_dev`` / ``<main>_test`` without confirmation).
    """
    from app.core.config import get_settings
    from app.core.database import check_migration_target, parse_choice, resolve_database_url

    settings = get_settings()
    args = context.get_x_argument(as_dictionary=True)
    url = config.get_main_option("sqlalchemy.url") or resolve_database_url(settings, parse_choice(args.get("database")))
    check_migration_target(url, settings.database_url.get_secret_value(), confirm=args.get("confirm"))
    return url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool, connect_args=CONNECT_ARGS)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
