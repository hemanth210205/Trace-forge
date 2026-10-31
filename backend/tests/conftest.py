"""Integration fixtures against the real PostgreSQL test database (TEST_DATABASE_URL).

The suite refuses to start unless TEST_DATABASE_URL names exactly ``<DATABASE_URL database>_test``
(``traceforge_test`` for ``traceforge``). The check runs in ``pytest_configure``, before collection and
before any fixture can connect or migrate. The suite migrates that database to head once per session and
truncates the M2 tables before every test.
"""

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import CONNECT_ARGS
from app.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[1]
TABLES = (
    "experiment_decisions", "experiments",
    "diagnosis_hypotheses", "diagnosis_cases", "diagnoses",
    "evaluation_results", "evaluation_runs", "dataset_version_cases", "dataset_versions", "dataset_cases", "datasets",
    "spans", "traces", "audit_log", "project_api_keys", "projects", "sessions", "users",
)
PASSWORD = "correct horse battery staple"

# Test-only secrets, so the suite never depends on (or exercises) the real .env values.
TEST_SECRET_KEY = "test-secret-key-" + "s" * 32
TEST_API_KEY_PEPPER = "test-api-key-pepper-" + "p" * 32


TRUSTED_ORIGIN = "http://localhost:5173"
LOGIN_MAX_FAILURES = 3
LOGIN_MAX_FAILURES_PER_IP = 10
LOGIN_WINDOW_SECONDS = 60

_settings: Settings | None = None


def unsafe_test_database_reason(test_url: str | None, main_url: str) -> str | None:
    """Why ``test_url`` must not be used by the suite, or None if it is the dedicated test database."""
    if not test_url:
        return "TEST_DATABASE_URL is not set."
    test_db = make_url(test_url).database
    expected = f"{make_url(main_url).database}_test"
    if test_db != expected:
        return f"TEST_DATABASE_URL must name the dedicated {expected!r} database, not {test_db!r}."
    return None


def pytest_configure(config: pytest.Config) -> None:
    global _settings
    base = Settings(TRACEFORGE_SECRET_KEY=TEST_SECRET_KEY, TRACEFORGE_API_KEY_PEPPER=TEST_API_KEY_PEPPER)  # type: ignore[call-arg]
    test_url = base.test_database_url.get_secret_value() if base.test_database_url else None
    reason = unsafe_test_database_reason(test_url, base.database_url.get_secret_value())
    if reason is not None:
        pytest.exit(f"Refusing to run the integration suite: {reason}", returncode=2)
    assert test_url is not None
    _settings = base.model_copy(
        update={
            "database_url": SecretStr(test_url),
            "cookie_secure": False,
            "log_level": "WARNING",
            "cors_origins": TRUSTED_ORIGIN,
            "login_max_failures": LOGIN_MAX_FAILURES,
            "login_max_failures_per_ip": LOGIN_MAX_FAILURES_PER_IP,
            "login_failure_window_seconds": LOGIN_WINDOW_SECONDS,
            # Recovery tests call the sweep directly; no background sweeper thread during the suite.
            "recovery_interval_seconds": 0,
            # TestClient sends Host: testserver; console serving is tested with its own build directory.
            "allowed_hosts": "127.0.0.1,localhost,testserver",
            "frontend_dist": "",
        }
    )


@pytest.fixture(scope="session")
def settings() -> Settings:
    assert _settings is not None
    return _settings


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    url = settings.database_url.get_secret_value()
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    # Full down/up cycle on the test database also proves the migration's downgrade path.
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    engine = create_engine(url, connect_args=CONNECT_ARGS)
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(TABLES)} CASCADE"))


@pytest.fixture(scope="session")
def app(settings: Settings, engine: Engine) -> FastAPI:
    return create_app(settings)


@pytest.fixture(autouse=True)
def _reset_login_limiter(app: FastAPI) -> Iterator[None]:
    app.state.login_limiter.reset()
    yield
    app.state.login_limiter.set_clock(time.monotonic)


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


class ApiClient(TestClient):
    """TestClient that remembers the CSRF token returned by register/login."""

    def remember_auth(self, body: dict[str, Any]) -> dict[str, Any]:
        self.headers["X-CSRF-Token"] = body["csrf_token"]
        return body["user"]

    def register(self, email: str, password: str = PASSWORD, display_name: str = "Test User") -> dict[str, Any]:
        response = self.post(
            "/api/v1/auth/register", json={"email": email, "password": password, "display_name": display_name}
        )
        assert response.status_code == 201, response.text
        return self.remember_auth(response.json())

    def create_project(self, name: str = "Support Agent", **fields: Any) -> dict[str, Any]:
        response = self.post("/api/v1/projects", json={"name": name, "agent_name": "support-bot", **fields})
        assert response.status_code == 201, response.text
        return response.json()

    def create_api_key(self, project_id: str, name: str = "ci") -> dict[str, Any]:
        response = self.post(f"/api/v1/projects/{project_id}/api-keys", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()


@pytest.fixture
def make_client(app: FastAPI) -> Iterator[Any]:
    clients: list[ApiClient] = []

    def factory(ip: str = "testclient", raise_server_exceptions: bool = True) -> ApiClient:
        client = ApiClient(app, client=(ip, 50000), raise_server_exceptions=raise_server_exceptions)
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def client(make_client: Any) -> ApiClient:
    return make_client()


@pytest.fixture
def alice(make_client: Any) -> ApiClient:
    c = make_client()
    c.register("alice@example.com", display_name="Alice")
    return c


@pytest.fixture
def bob(make_client: Any) -> ApiClient:
    c = make_client()
    c.register("bob@example.com", display_name="Bob")
    return c
