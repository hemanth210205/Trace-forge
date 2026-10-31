import os
import subprocess
import sys

import pytest
from sqlalchemy.engine import make_url

from app.dev_test_server import UnsafeDatabase, resolve_dev_database_url, resolve_test_database_url
from tests.conftest import BACKEND_DIR

MAIN = "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge"


def test_accepts_only_the_dedicated_test_database() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge_test"
    assert resolve_test_database_url(MAIN, url) == url


@pytest.mark.parametrize("db", [None, "traceforge", "postgres", "other_test", "traceforge_testing"])
def test_refuses_any_other_database(db: str | None) -> None:
    with pytest.raises(UnsafeDatabase):
        resolve_test_database_url(MAIN, None if db is None else f"postgresql+psycopg://u:p@127.0.0.1:5433/{db}")


def test_process_refuses_to_start_on_the_real_database_without_leaking_credentials() -> None:
    from app.core.config import get_settings

    real_url = get_settings().database_url.get_secret_value()
    env = {**os.environ, "TEST_DATABASE_URL": real_url}
    result = subprocess.run(
        [sys.executable, "-m", "app.dev_test_server", "--port", "1"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2
    assert "Refusing to start" in result.stderr and "'traceforge'" in result.stderr
    password = make_url(real_url).password
    if password:
        assert password not in result.stderr + result.stdout


# --- --database dev -------------------------------------------------------------------------------------------


def test_dev_target_accepts_only_traceforge_dev() -> None:
    url = "postgresql+psycopg://u:p@127.0.0.1:5433/traceforge_dev"
    assert resolve_dev_database_url(MAIN, url) == url


@pytest.mark.parametrize("db", [None, "traceforge", "traceforge_test", "postgres", "other_dev", "traceforge_development"])
def test_dev_target_refuses_every_other_database(db: str | None) -> None:
    with pytest.raises(UnsafeDatabase):
        resolve_dev_database_url(MAIN, None if db is None else f"postgresql+psycopg://u:p@127.0.0.1:5433/{db}")


@pytest.mark.parametrize("target", ["database_url", "test_database_url"])
def test_dev_process_refuses_the_real_and_test_databases(target: str) -> None:
    from app.core.config import get_settings

    wrong_url = getattr(get_settings(), target).get_secret_value()
    env = {**os.environ, "DEV_DATABASE_URL": wrong_url}
    result = subprocess.run(
        [sys.executable, "-m", "app.dev_test_server", "--database", "dev", "--port", "1"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 2
    assert "Refusing to start: DEV_DATABASE_URL must name 'traceforge_dev'" in result.stderr
    password = make_url(wrong_url).password
    if password:
        assert password not in result.stderr + result.stdout
