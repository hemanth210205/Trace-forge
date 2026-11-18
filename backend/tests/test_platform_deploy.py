"""Hosting-platform support (Render's free plan): driver-less database URLs, the public-bind opt-in, and the confirmed
migration of the main database at start. The migration test uses a scratch database it creates and drops itself."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import serve
from app.core.config import Settings
from app.core.database import CHOICE_ENV
from app.db.schema import head_revision
from tests.conftest import TEST_API_KEY_PEPPER, TEST_SECRET_KEY

BASE = {"TRACEFORGE_SECRET_KEY": TEST_SECRET_KEY, "TRACEFORGE_API_KEY_PEPPER": TEST_API_KEY_PEPPER}


def make(**env: Any) -> Settings:
    return Settings(**BASE, **env)  # type: ignore[arg-type]


# --- database URLs ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("given", ["postgres://u:p@db.example:5432/traceforge", "postgresql://u:p@db.example:5432/traceforge"])
def test_platform_database_urls_get_the_psycopg_driver(given: str) -> None:
    settings = make(DATABASE_URL=given)
    assert settings.database_url.get_secret_value() == "postgresql+psycopg://u:p@db.example:5432/traceforge"


def test_urls_with_a_driver_or_parameters_are_kept() -> None:
    explicit = "postgresql+psycopg://u:p@db.example:5432/traceforge?sslmode=require"
    assert make(DATABASE_URL=explicit).database_url.get_secret_value() == explicit
    with_params = make(DATABASE_URL="postgresql://u:p@db.example/traceforge?sslmode=require")
    assert with_params.database_url.get_secret_value() == "postgresql+psycopg://u:p@db.example/traceforge?sslmode=require"


# --- public bind -----------------------------------------------------------------------------------------------


def test_public_bind_is_off_by_default() -> None:
    assert make().public_bind is False


@pytest.mark.parametrize("env", [
    {"TRACEFORGE_PUBLIC_BIND": "true", "TRACEFORGE_COOKIE_SECURE": "true"},                          # no trusted proxy
    {"TRACEFORGE_PUBLIC_BIND": "true", "TRACEFORGE_TRUSTED_PROXIES": "10.0.0.0/8"},                   # cookies not Secure
    {"TRACEFORGE_PUBLIC_BIND": "true", "TRACEFORGE_TRUSTED_PROXIES": " ", "TRACEFORGE_COOKIE_SECURE": "true"},
])
def test_public_bind_requires_trusted_proxies_and_secure_cookies(env: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="TRACEFORGE_PUBLIC_BIND"):
        make(**env)


def test_public_bind_accepted_with_its_safeguards() -> None:
    settings = make(TRACEFORGE_PUBLIC_BIND="true", TRACEFORGE_TRUSTED_PROXIES="10.0.0.0/8", TRACEFORGE_COOKIE_SECURE="true")
    assert settings.public_bind is True


@pytest.fixture
def no_server(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stops app.serve.main before it touches a database or starts uvicorn; records what it would have done."""
    calls: dict[str, Any] = {}
    monkeypatch.setattr(serve, "prepare", lambda choice: calls.setdefault("prepare", choice) and "database 'x'")
    monkeypatch.setattr(serve.uvicorn, "run", lambda *a, **kw: calls.setdefault("run", kw))
    return calls


def use_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    import app.core.config as config

    getter = lambda: settings  # noqa: E731
    getter.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(config, "get_settings", getter)


def test_public_address_is_refused_without_the_opt_in(monkeypatch: pytest.MonkeyPatch, no_server: dict[str, Any]) -> None:
    use_settings(monkeypatch, make())
    with pytest.raises(SystemExit) as exc:
        serve.main(["--database", "main", "--host", "0.0.0.0"])
    assert exc.value.code == 2 and no_server == {}


def test_public_address_is_refused_for_dev_and_test_databases(monkeypatch: pytest.MonkeyPatch, no_server: dict[str, Any]) -> None:
    use_settings(monkeypatch, make(TRACEFORGE_PUBLIC_BIND="true", TRACEFORGE_TRUSTED_PROXIES="10.0.0.0/8", TRACEFORGE_COOKIE_SECURE="true"))
    for database in ("dev", "test"):
        with pytest.raises(SystemExit):
            serve.main(["--database", database, "--host", "0.0.0.0"])
    assert no_server == {}


def test_public_address_is_served_with_the_opt_in(monkeypatch: pytest.MonkeyPatch, no_server: dict[str, Any]) -> None:
    use_settings(monkeypatch, make(TRACEFORGE_PUBLIC_BIND="true", TRACEFORGE_TRUSTED_PROXIES="10.0.0.0/8", TRACEFORGE_COOKIE_SECURE="true"))
    serve.main(["--database", "main", "--host", "0.0.0.0", "--port", "10000"])
    assert no_server["prepare"] == "main"
    assert no_server["run"]["host"] == "0.0.0.0" and no_server["run"]["port"] == 10000
    # The same hardened options as the local server: uvicorn never trusts forwarded headers itself.
    assert no_server["run"]["proxy_headers"] is False and no_server["run"]["access_log"] is False


# --- migrating the main database at start ----------------------------------------------------------------------


@pytest.fixture
def scratch_database(settings: Settings) -> Iterator[str]:
    """A throwaway database on the test server, named so it can only ever be this test's."""
    name = f"tf_platform_check_{uuid.uuid4().hex[:8]}"
    admin_url = make_url(settings.database_url.get_secret_value()).set(database="postgres")
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield str(make_url(settings.database_url.get_secret_value()).set(database=name).render_as_string(hide_password=False))
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_main_is_not_migrated_without_confirmation(monkeypatch: pytest.MonkeyPatch, scratch_database: str) -> None:
    use_settings(monkeypatch, make(DATABASE_URL=scratch_database))
    monkeypatch.setenv(CHOICE_ENV, "test")
    with pytest.raises(SystemExit) as exc:
        serve.prepare("main")  # empty database, no opt-in: refuses instead of migrating
    assert exc.value.code == 2


def test_wrong_confirmation_name_is_refused_before_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    # Points at a host that does not exist: the refusal must come before any connection attempt.
    use_settings(monkeypatch, make(DATABASE_URL="postgresql://u:p@nonexistent.invalid:5432/traceforge",
                                   TRACEFORGE_MIGRATE_MAIN_ON_START="true"))
    monkeypatch.setenv(CHOICE_ENV, "test")
    with pytest.raises(SystemExit) as exc:
        serve.prepare("main")
    assert exc.value.code == 2


def test_confirmed_main_database_is_upgraded_to_head_at_start(monkeypatch: pytest.MonkeyPatch, scratch_database: str) -> None:
    name = make_url(scratch_database).database
    use_settings(monkeypatch, make(DATABASE_URL=scratch_database, TRACEFORGE_MIGRATE_MAIN_ON_START=name))
    monkeypatch.setenv(CHOICE_ENV, "test")
    description = serve.prepare("main")
    assert f"at revision {head_revision()}" in description
    engine = create_engine(scratch_database)
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar() == head_revision()
        assert conn.execute(text("select count(*) from invitations")).scalar() == 0
    engine.dispose()
    # Starting again is a no-op at head.
    assert f"at revision {head_revision()}" in serve.prepare("main")
