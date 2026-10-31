from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


# libpq connection parameters passed through psycopg. connect_timeout (seconds) makes an
# unreachable database fail fast instead of hanging.
CONNECT_ARGS = {"connect_timeout": 10, "options": "-c timezone=UTC"}


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, connect_args=CONNECT_ARGS)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    """One session per request; rolled back on error, closed always."""
    session: Session = request.app.state.sessionmaker()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
