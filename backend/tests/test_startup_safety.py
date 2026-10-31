"""Local-release startup safety: database selection, the migration guard, schema checks, hosts and the console.

Nothing here connects to the main database. Cases about it use fake URLs on an unreachable port (127.0.0.1:1),
so the guards are proven to decide before any connection is attempted.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from app.core.config import Settings, get_settings
from app.core.database import (
    UnsafeDatabase,
    check_migration_target,
    parse_choice,
    resolve_database_url,
)
from app.db import schema
from app.db.schema import SchemaOutOfDate, migrate_to_head
from app.main import create_app
from tests.conftest import BACKEND_DIR, PASSWORD, TEST_API_KEY_PEPPER, TEST_SECRET_KEY, ApiClient

FAKE = "postgresql+psycopg://u:p@127.0.0.1:1/{}"
FAKE_ENV = {
    "DATABASE_URL": FAKE.format("traceforge"), "DEV_DATABASE_URL": FAKE.format("traceforge_dev"),
    "TEST_DATABASE_URL": FAKE.format("traceforge_test"),
    "TRACEFORGE_SECRET_KEY": TEST_SECRET_KEY, "TRACEFORGE_API_KEY_PEPPER": TEST_API_KEY_PEPPER,
}


def fake_settings(**overrides: str) -> Settings:
    values = {**FAKE_ENV, **overrides}
    return Settings(**values)  # type: ignore[arg-type]


# --- database selection --------------------------------------------------------------------------------------


def test_the_default_is_the_development_database() -> None:
    assert parse_choice(None) == parse_choice("") == "dev"
    assert make_url(resolve_database_url(fake_settings(), parse_choice(None))).database == "traceforge_dev"
    assert make_url(resolve_database_url(fake_settings(), "test")).database == "traceforge_test"


def test_the_main_database_needs_an_explicit_choice() -> None:
    assert make_url(resolve_database_url(fake_settings(), "main")).database == "traceforge"
    for value in ("prod", "traceforge", "MAIN ", "1"):
        if value.strip().lower() != "main":
            with pytest.raises(UnsafeDatabase):
                parse_choice(value)


@pytest.mark.parametrize("dev", [FAKE.format("traceforge"), FAKE.format("traceforge_test"), FAKE.format("other_dev")])
def test_a_misconfigured_dev_url_is_refused_not_used(dev: str) -> None:
    with pytest.raises(UnsafeDatabase, match="DEV_DATABASE_URL must name 'traceforge_dev'"):
        resolve_database_url(fake_settings(DEV_DATABASE_URL=dev), "dev")


def test_create_app_without_settings_uses_the_dev_database(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TRACEFORGE_DATABASE", raising=False)
    get_settings.cache_clear()
    try:
        app = create_app()  # builds a lazy engine only; nothing connects
        assert make_url(app.state.settings.database_url.get_secret_value()).database == "traceforge_dev"
        monkeypatch.setenv("TRACEFORGE_DATABASE", "staging")
        with pytest.raises(UnsafeDatabase):
            create_app()
    finally:
        get_settings.cache_clear()


# --- migration guard ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("target", "confirm", "allowed"), [
    ("traceforge_dev", None, True), ("traceforge_test", None, True),
    ("traceforge", None, False), ("traceforge", "yes", False), ("traceforge", "traceforge", True),
    ("postgres", None, False), ("traceforge_dev2", None, False), ("other", "traceforge", False),
])
def test_migration_target_guard(target: str, confirm: str | None, allowed: bool) -> None:
    if allowed:
        assert check_migration_target(FAKE.format(target), FAKE.format("traceforge"), confirm=confirm) == target
    else:
        with pytest.raises(UnsafeDatabase):
            check_migration_target(FAKE.format(target), FAKE.format("traceforge"), confirm=confirm)


def test_migrate_to_head_refuses_before_connecting() -> None:
    with pytest.raises(UnsafeDatabase):
        migrate_to_head(FAKE.format("traceforge"), FAKE.format("traceforge"))


def alembic(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "TRACEFORGE_DATABASE"} | FAKE_ENV
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=120)


def test_alembic_cli_refuses_the_main_database_without_confirmation() -> None:
    result = alembic("-x", "database=main", "upgrade", "head")
    assert result.returncode != 0
    assert "Refusing to migrate the main database 'traceforge' without confirmation" in result.stderr
    assert "connection" not in result.stderr.lower()  # refused before any connection attempt
    assert "u:p@" not in result.stderr + result.stdout


def test_alembic_cli_passes_the_guard_only_with_the_exact_name() -> None:
    confirmed = alembic("-x", "database=main", "-x", "confirm=traceforge", "current")
    assert "Refusing" not in confirmed.stderr and confirmed.returncode != 0  # got as far as the (unreachable) server
    default = alembic("current")  # no -x: the dev database
    assert "Refusing" not in default.stderr and default.returncode != 0


# --- schema revision ---------------------------------------------------------------------------------------


def test_startup_refuses_a_database_at_another_revision(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(schema, "head_revision", lambda: "9999")
    with pytest.raises(SchemaOutOfDate, match="needs 9999. Nothing was changed"):
        with ApiClient(create_app(settings)):
            pass


def test_serve_refuses_non_loopback_and_unknown_databases() -> None:
    env = {**os.environ, **FAKE_ENV}
    for args in (["--host", "0.0.0.0"], ["--database", "prod"]):
        result = subprocess.run([sys.executable, "-m", "app.serve", *args, "--port", "1"], cwd=BACKEND_DIR, env=env,
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 2, result.stderr
        assert "u:p@" not in result.stderr + result.stdout


# --- hosts and origins -------------------------------------------------------------------------------------


def test_same_origin_console_can_register_and_login(app: FastAPI) -> None:
    client = ApiClient(app, base_url="http://127.0.0.1:8000")
    origin = {"Origin": "http://127.0.0.1:8000"}
    assert client.post("/api/v1/auth/register", json={"email": "ada@example.com", "password": PASSWORD, "display_name": "Ada"},
                       headers=origin).status_code == 201
    assert client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}, headers=origin).status_code == 200
    other_port = client.post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD},
                             headers={"Origin": "http://127.0.0.1:9999"})
    assert other_port.status_code == 403


def test_foreign_host_names_are_rejected_which_blocks_dns_rebinding(app: FastAPI) -> None:
    rebinding = ApiClient(app, base_url="http://attacker.example:8000")
    response = rebinding.post("/api/v1/auth/register", json={"email": "x@example.com", "password": PASSWORD, "display_name": "X"},
                              headers={"Origin": "http://attacker.example:8000"})
    assert response.status_code == 400
    assert rebinding.get("/api/v1/health").status_code == 400


# --- console serving ---------------------------------------------------------------------------------------


@pytest.fixture
def console_app(settings: Settings, tmp_path: Path) -> FastAPI:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Traceforge</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('tf')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside dist", encoding="utf-8")
    return create_app(settings.model_copy(update={"frontend_dist": str(dist)}))


def test_console_routes_get_the_app_shell_with_a_strict_policy(console_app: FastAPI) -> None:
    client = ApiClient(console_app)
    for path in ("/", "/login", "/projects/abc/traces", "/projects/abc/experiments/new?source_run=x"):
        response = client.get(path)
        assert response.status_code == 200 and "<div id=root>" in response.text, path
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/assets/app.js").text == "console.log('tf')"
    missing = client.get("/assets/missing.js")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_console_never_shadows_or_leaks(console_app: FastAPI) -> None:
    client = ApiClient(console_app)
    assert client.get("/api/v1/health").json()["status"] == "ok"
    unknown = client.get("/api/v1/does-not-exist")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    assert client.get("/api").json()["error"]["code"] == "not_found"
    assert client.post("/api/v1/health").status_code == 405  # API method handling unchanged
    assert client.get("/api/docs").status_code == 200
    for path in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"):
        assert "outside dist" not in client.get(path).text, path
    assert not any("{path" in p for p in console_app.openapi()["paths"])


def test_without_a_build_nothing_is_served_at_the_root(app: FastAPI, client: ApiClient) -> None:
    assert client.get("/").status_code == 404
    assert client.get("/projects/abc/traces").json()["error"]["code"] == "not_found"


def test_settings_secret_values_are_not_part_of_the_console_route(console_app: FastAPI) -> None:
    secret = console_app.state.settings.secret_key
    assert isinstance(secret, SecretStr)
    assert secret.get_secret_value() not in ApiClient(console_app).get("/").text
