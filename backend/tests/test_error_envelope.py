"""Error responses produced outside the routers (413 size limit, unhandled 500) keep the envelope and headers."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests.conftest import TRUSTED_ORIGIN, ApiClient


@pytest.fixture(scope="module")
def small_app(settings: Settings) -> FastAPI:
    app = create_app(settings.model_copy(update={"max_request_bytes": 1024}))

    def explode() -> None:
        raise RuntimeError("secret-looking detail sk-should-not-leak")

    app.add_api_route("/api/v1/_explode", explode, methods=["GET"])
    return app


def assert_envelope(response, status: int, code: str) -> None:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code and error["request_id"]
    assert response.headers["x-request-id"] == error["request_id"]
    assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN


def test_declared_oversized_body_is_a_readable_413(small_app: FastAPI) -> None:
    client = ApiClient(small_app)
    response = client.post("/api/v1/auth/login", content=b"x" * 2048,
                           headers={"Origin": TRUSTED_ORIGIN, "Content-Type": "application/json"})
    assert_envelope(response, 413, "payload_too_large")


def test_streamed_oversized_body_is_a_readable_413(small_app: FastAPI) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(4):
            yield b"x" * 512

    client = ApiClient(small_app)
    response = client.post("/api/v1/ingest/batch", content=chunks(),
                           headers={"Origin": TRUSTED_ORIGIN, "Content-Type": "application/json", "Authorization": "Bearer tf_x_y"})
    assert_envelope(response, 413, "payload_too_large")


def test_unhandled_error_is_a_readable_500_without_details(small_app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    client = ApiClient(small_app, raise_server_exceptions=False)
    response = client.get("/api/v1/_explode", headers={"Origin": TRUSTED_ORIGIN, "X-Request-ID": "req-123"})
    assert_envelope(response, 500, "internal_error")
    assert response.json()["error"]["request_id"] == "req-123"
    assert "sk-should-not-leak" not in response.text
    assert "unhandled error: RuntimeError" in caplog.text


def test_unhandled_errors_still_reach_test_clients(small_app: FastAPI) -> None:
    with pytest.raises(RuntimeError):
        ApiClient(small_app).get("/api/v1/_explode")


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/auth/me", "/api/v1/_explode"])
def test_every_response_carries_security_headers(small_app: FastAPI, path: str) -> None:
    response = ApiClient(small_app, raise_server_exceptions=False).get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
