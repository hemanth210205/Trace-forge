"""Runtime checks against real uvicorn servers over real sockets (TestClient bypasses uvicorn's
ProxyHeadersMiddleware, so it cannot prove anything about forwarded headers).

1. In-process: the test app is served by ``uvicorn.Server`` with ``app.serve.SERVER_OPTIONS``, wrapped in a
   recorder that captures the client address the application receives.
2. Subprocess: both documented startup commands are launched as real processes with a hostile
   environment that tries to re-enable proxy trust, and checked through the login rate limiter.

Each has a negative control running uvicorn's defaults, proving the test detects header trust.
"""

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.core.config import Settings
from app.serve import SERVER_OPTIONS
from tests.conftest import BACKEND_DIR, TEST_API_KEY_PEPPER, TEST_SECRET_KEY, TRUSTED_ORIGIN

SPOOFED = [
    {"X-Forwarded-For": "203.0.113.1"},
    {"X-Forwarded-For": "198.51.100.2"},
    {"X-Forwarded-For": "10.0.0.1, 10.0.0.2"},
    {"X-Real-IP": "192.0.2.3"},
    {"X-Forwarded-For": "192.0.2.4", "X-Real-IP": "192.0.2.5", "X-Forwarded-Proto": "https"},
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def serve_in_thread(asgi_app: Any, **options: Any) -> Iterator[str]:
    port = free_port()
    config = uvicorn.Config(asgi_app, host="127.0.0.1", port=port, log_level="warning", lifespan="off", **options)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def recording(asgi_app: Any, seen: list[tuple[str, str]]) -> Any:
    """ASGI wrapper placed inside uvicorn's middleware: records what the application is handed."""

    async def wrapper(scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            seen.append((scope["client"][0], scope["scheme"]))
        await asgi_app(scope, receive, send)

    return wrapper


def test_documented_server_options_ignore_forwarded_headers(app: FastAPI) -> None:
    seen: list[tuple[str, str]] = []
    with serve_in_thread(recording(app, seen), **SERVER_OPTIONS) as base_url:
        for headers in SPOOFED:
            assert httpx.get(f"{base_url}/api/v1/health", headers=headers).status_code == 200
    assert seen == [("127.0.0.1", "http")] * len(SPOOFED)


def test_control_uvicorn_defaults_do_trust_forwarded_headers(app: FastAPI) -> None:
    """Negative control: with uvicorn's defaults the same requests *do* change the client address."""
    seen: list[tuple[str, str]] = []
    with serve_in_thread(recording(app, seen)) as base_url:
        for headers in SPOOFED[:2]:
            httpx.get(f"{base_url}/api/v1/health", headers=headers)
    assert [ip for ip, _ in seen] == ["203.0.113.1", "198.51.100.2"]


def test_rotating_forwarded_headers_cannot_reset_the_login_budget(app: FastAPI, settings: Settings) -> None:
    with serve_in_thread(app, **SERVER_OPTIONS) as base_url:
        codes = [
            httpx.post(
                f"{base_url}/api/v1/auth/login",
                json={"email": "ghost@example.com", "password": "wrong"},
                headers={"X-Forwarded-For": f"203.0.113.{i}", "X-Real-IP": f"198.51.100.{i}"},
            ).status_code
            for i in range(settings.login_max_failures + 2)
        ]
    assert codes == [401] * settings.login_max_failures + [429, 429]


# --- Documented startup commands, as real processes ----------------------------------------------------

DOCUMENTED_COMMANDS = {
    "python -m app.serve": [sys.executable, "-m", "app.serve", "--database", "test", "--port", "{port}"],
    "uvicorn --no-proxy-headers": [
        sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory",
        "--host", "127.0.0.1", "--port", "{port}", "--no-proxy-headers",
    ],
}
CONTROL_COMMAND = [
    sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "{port}",
]
# Every documented knob that could switch proxy-header trust back on.
HOSTILE_ENV = {"FORWARDED_ALLOW_IPS": "*", "UVICORN_PROXY_HEADERS": "true", "UVICORN_FORWARDED_ALLOW_IPS": "*"}
MAX_FAILURES = 2


@contextmanager
def server_process(command: list[str], settings: Settings) -> Iterator[str]:
    port = free_port()
    env = {
        **os.environ,
        **HOSTILE_ENV,
        # Select the dedicated test database (uvicorn --factory reads TRACEFORGE_DATABASE; app.serve gets
        # --database test) with test-only secrets; .env is not modified.
        "TRACEFORGE_DATABASE": "test",
        "TRACEFORGE_SECRET_KEY": TEST_SECRET_KEY,
        "TRACEFORGE_API_KEY_PEPPER": TEST_API_KEY_PEPPER,
        "TRACEFORGE_CORS_ORIGINS": TRUSTED_ORIGIN,
        "TRACEFORGE_LOGIN_MAX_FAILURES": str(MAX_FAILURES),
        "TRACEFORGE_LOGIN_MAX_FAILURES_PER_IP": "100",
        "TRACEFORGE_LOG_LEVEL": "WARNING",
    }
    process = subprocess.Popen(
        [part.format(port=port) for part in command],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            if process.poll() is not None:
                raise RuntimeError(f"server exited early: {process.stderr.read().decode(errors='replace')[-2000:]}")
            try:
                if httpx.get(f"{base_url}/api/v1/health", timeout=1).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("server did not become healthy")
            time.sleep(0.2)
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=15)


def login_codes_with_rotating_forwarded_for(base_url: str) -> list[int]:
    return [
        httpx.post(
            f"{base_url}/api/v1/auth/login",
            json={"email": "ghost@example.com", "password": "wrong"},
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        ).status_code
        for i in range(MAX_FAILURES + 1)
    ]


@pytest.mark.parametrize("name", list(DOCUMENTED_COMMANDS))
def test_documented_startup_commands_ignore_forwarded_headers(name: str, settings: Settings) -> None:
    with server_process(DOCUMENTED_COMMANDS[name], settings) as base_url:
        assert login_codes_with_rotating_forwarded_for(base_url) == [401] * MAX_FAILURES + [429]


def test_control_plain_uvicorn_command_is_spoofable(settings: Settings) -> None:
    """Negative control: without --no-proxy-headers, rotating X-Forwarded-For dodges the limiter."""
    with server_process(CONTROL_COMMAND, settings) as base_url:
        assert login_codes_with_rotating_forwarded_for(base_url) == [401] * (MAX_FAILURES + 1)
