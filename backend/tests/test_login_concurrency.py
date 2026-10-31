"""Simultaneous login attempts through the full HTTP stack: the reservation must hold the budget.

``verify_password`` is wrapped to be slow so that every request is in flight at the same time; the
number of calls that reach it is the number of password guesses the limiter let through.
"""

import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi import FastAPI

from app.api.routes import auth as auth_routes
from tests.conftest import LOGIN_MAX_FAILURES, LOGIN_MAX_FAILURES_PER_IP, PASSWORD, ApiClient

LOGIN = "/api/v1/auth/login"
IP = "testclient"


class SlowVerify:
    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()
        self._original = auth_routes.verify_password

    def __call__(self, password_hash: str | None, password: str) -> bool:
        with self._lock:
            self.calls += 1
        time.sleep(self.delay)
        return self._original(password_hash, password)


@pytest.fixture
def slow_verify(monkeypatch: pytest.MonkeyPatch) -> SlowVerify:
    wrapper = SlowVerify()
    monkeypatch.setattr(auth_routes, "verify_password", wrapper)
    return wrapper


def simultaneously(make_client: Callable[..., ApiClient], payloads: list[dict[str, Any]], **client_kwargs: Any) -> list[Any]:
    clients = [make_client(**client_kwargs) for _ in payloads]
    barrier = threading.Barrier(len(payloads))

    def send(index: int) -> Any:
        barrier.wait()
        return clients[index].post(LOGIN, json=payloads[index])

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return list(pool.map(send, range(len(payloads))))


def statuses(responses: list[Any]) -> Counter[int]:
    return Counter(r.status_code for r in responses)


def limiter_of(app: FastAPI) -> Any:
    return app.state.login_limiter


def test_simultaneous_guesses_cannot_exceed_the_per_account_budget(
    alice: ApiClient, make_client, slow_verify: SlowVerify, app: FastAPI
) -> None:
    responses = simultaneously(make_client, [{"email": "alice@example.com", "password": "wrong guess"}] * 20)
    assert slow_verify.calls == LOGIN_MAX_FAILURES
    assert statuses(responses) == {401: LOGIN_MAX_FAILURES, 429: 20 - LOGIN_MAX_FAILURES}
    for r in responses:
        if r.status_code == 429:
            assert int(r.headers["retry-after"]) >= 1
    key = limiter_of(app).account_key(IP, "alice@example.com")
    assert limiter_of(app).per_account.failure_count(key) == LOGIN_MAX_FAILURES
    assert limiter_of(app).per_account.pending_count(key) == 0


def test_simultaneous_spraying_cannot_exceed_the_per_ip_budget(
    make_client, slow_verify: SlowVerify, app: FastAPI
) -> None:
    payloads = [{"email": f"victim{i}@example.com", "password": "wrong guess"} for i in range(30)]
    responses = simultaneously(make_client, payloads)
    assert slow_verify.calls == LOGIN_MAX_FAILURES_PER_IP
    assert statuses(responses) == {401: LOGIN_MAX_FAILURES_PER_IP, 429: 30 - LOGIN_MAX_FAILURES_PER_IP}
    assert limiter_of(app).per_ip.failure_count(IP) == LOGIN_MAX_FAILURES_PER_IP
    assert limiter_of(app).per_ip.pending_count(IP) == 0


def test_simultaneous_successful_logins_release_their_reservations(
    alice: ApiClient, make_client, slow_verify: SlowVerify, app: FastAPI
) -> None:
    # Two IP-wide failures on other emails first: they must survive the successful logins.
    for email in ("x@example.com", "y@example.com"):
        assert make_client().post(LOGIN, json={"email": email, "password": "nope"}).status_code == 401

    responses = simultaneously(make_client, [{"email": "alice@example.com", "password": PASSWORD}] * LOGIN_MAX_FAILURES)
    assert statuses(responses) == {200: LOGIN_MAX_FAILURES}

    limiter = limiter_of(app)
    key = limiter.account_key(IP, "alice@example.com")
    assert limiter.per_account.pending_count(key) == 0 and limiter.per_account.failure_count(key) == 0
    assert limiter.per_ip.pending_count(IP) == 0
    assert limiter.per_ip.failure_count(IP) == 2  # not erased by the successes

    # The whole account budget is available again.
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES):
        assert client.post(LOGIN, json={"email": "alice@example.com", "password": "nope"}).status_code == 401
    assert client.post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD}).status_code == 429


def test_more_simultaneous_correct_logins_than_budget_are_throttled_briefly(
    alice: ApiClient, make_client, slow_verify: SlowVerify, app: FastAPI
) -> None:
    responses = simultaneously(make_client, [{"email": "alice@example.com", "password": PASSWORD}] * 8)
    assert statuses(responses) == {200: LOGIN_MAX_FAILURES, 429: 8 - LOGIN_MAX_FAILURES}
    assert {r.headers["retry-after"] for r in responses if r.status_code == 429} == {"1"}
    assert limiter_of(app).per_ip.pending_count(IP) == 0
    assert make_client().post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200


def test_exception_during_verification_releases_reservations(
    alice: ApiClient, make_client, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    def explode(*_args: object) -> bool:
        raise RuntimeError("hasher crashed")

    monkeypatch.setattr(auth_routes, "verify_password", explode)
    client = make_client(raise_server_exceptions=False)
    for _ in range(LOGIN_MAX_FAILURES * 3):  # well past the budget: nothing may get stuck
        response = client.post(LOGIN, json={"email": "alice@example.com", "password": "x"})
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
    limiter = limiter_of(app)
    assert limiter.per_ip.pending_count(IP) == 0 and limiter.per_ip.failure_count(IP) == 0

    monkeypatch.undo()
    assert make_client().post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200


def test_simultaneous_exceptions_do_not_leave_reservations_behind(
    alice: ApiClient, make_client, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    def slow_explode(*_args: object) -> bool:
        time.sleep(0.2)
        raise RuntimeError("hasher crashed")

    monkeypatch.setattr(auth_routes, "verify_password", slow_explode)
    responses = simultaneously(
        make_client, [{"email": "alice@example.com", "password": "x"}] * 10, raise_server_exceptions=False
    )
    assert statuses(responses)[500] == LOGIN_MAX_FAILURES  # only reserved attempts reached the hasher
    assert limiter_of(app).per_ip.pending_count(IP) == 0


def test_exception_after_successful_verification_does_not_leak_reservation(
    alice: ApiClient, make_client, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    def broken_session(*_args: object) -> str:
        raise RuntimeError("session store down")

    monkeypatch.setattr(auth_routes, "_start_session", broken_session)
    response = make_client(raise_server_exceptions=False).post(
        LOGIN, json={"email": "alice@example.com", "password": PASSWORD}
    )
    assert response.status_code == 500
    assert limiter_of(app).per_ip.pending_count(IP) == 0


def test_exception_inside_finalization_does_not_deadlock_or_stick(
    alice: ApiClient, make_client, monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    limiter = limiter_of(app)

    class Clock:
        now = 50_000.0

        def __call__(self) -> float:
            return self.now

    clock = Clock()
    limiter.set_clock(clock)

    def broken_commit(*_args: object) -> None:
        raise RuntimeError("bug while recording failure")

    monkeypatch.setattr(limiter.per_ip, "commit_failure", broken_commit)
    client = make_client(raise_server_exceptions=False)
    assert client.post(LOGIN, json={"email": "alice@example.com", "password": "wrong"}).status_code == 500
    monkeypatch.undo()

    # The limiter lock was released: the next request completes promptly.
    started = time.monotonic()
    assert client.post(LOGIN, json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200
    assert time.monotonic() - started < 5
    # The orphaned per-IP reservation expires after the TTL rather than holding the budget forever.
    assert limiter.per_ip.pending_count(IP) == 1
    clock.now += limiter.per_ip.reservation_ttl_seconds
    assert limiter.per_ip.pending_count(IP) == 0
