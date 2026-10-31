"""Origin validation and failed-login rate limiting on the cookie-issuing endpoints."""

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import auth as auth_routes
from app.core.rate_limit import LoginRateLimiter
from app.models import User, UserSession
from tests.conftest import (
    LOGIN_MAX_FAILURES,
    LOGIN_MAX_FAILURES_PER_IP,
    LOGIN_WINDOW_SECONDS,
    PASSWORD,
    TRUSTED_ORIGIN,
    ApiClient,
)

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
CREDENTIALS = {"email": "alice@example.com", "password": PASSWORD}
NEW_ACCOUNT = {"email": "new@example.com", "password": PASSWORD, "display_name": "New"}
EVIL = "https://evil.example"


def count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


# --- Origin validation ------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [LOGIN, REGISTER])
def test_trusted_origin_is_accepted(alice: ApiClient, make_client, path: str) -> None:
    payload = CREDENTIALS if path == LOGIN else NEW_ACCOUNT
    response = make_client().post(path, json=payload, headers={"Origin": TRUSTED_ORIGIN})
    assert response.status_code in (200, 201), response.text


@pytest.mark.parametrize(
    "origin",
    [
        EVIL,
        "null",  # sandboxed iframes, file:// pages, some redirects
        "http://localhost:5174",  # same host, different port
        "https://localhost:5173",  # same host and port, different scheme
        "http://localhost:5173.evil.example",
    ],
)
@pytest.mark.parametrize("path", [LOGIN, REGISTER])
def test_untrusted_origin_is_rejected_without_side_effects(
    alice: ApiClient, make_client, db: Session, path: str, origin: str
) -> None:
    sessions_before, users_before = count(db, UserSession), count(db, User)
    payload = CREDENTIALS if path == LOGIN else NEW_ACCOUNT
    response = make_client().post(path, json=payload, headers={"Origin": origin})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "untrusted_origin"
    assert "tf_session" not in response.cookies
    assert (count(db, UserSession), count(db, User)) == (sessions_before, users_before)


@pytest.mark.parametrize("path", [LOGIN, REGISTER])
def test_missing_origin_and_referer_is_allowed_for_non_browser_clients(alice: ApiClient, make_client, path: str) -> None:
    payload = CREDENTIALS if path == LOGIN else NEW_ACCOUNT
    response = make_client().post(path, json=payload)  # httpx sends neither header
    assert response.status_code in (200, 201), response.text


def test_referer_is_checked_when_origin_is_missing(alice: ApiClient, make_client) -> None:
    trusted = make_client().post(LOGIN, json=CREDENTIALS, headers={"Referer": f"{TRUSTED_ORIGIN}/login?next=/"})
    assert trusted.status_code == 200
    untrusted = make_client().post(LOGIN, json=CREDENTIALS, headers={"Referer": f"{EVIL}/phish"})
    assert untrusted.status_code == 403
    garbage = make_client().post(LOGIN, json=CREDENTIALS, headers={"Referer": "not a url"})
    assert garbage.status_code == 403


def test_origin_check_does_not_affect_other_endpoints(alice: ApiClient) -> None:
    # Authenticated writes rely on the CSRF token instead; the Origin check is only on login/register.
    response = alice.post("/api/v1/projects", json={"name": "P", "agent_name": "a"}, headers={"Origin": EVIL})
    assert response.status_code == 201


# --- Cross-site form submissions ----------------------------------------------------------------------
# An HTML form can only send these three content types, and a cross-site one always carries an Origin.

FORM_BODIES = [
    pytest.param({"data": {"email": "alice@example.com", "password": PASSWORD}}, id="urlencoded"),
    pytest.param(
        {"files": {"email": (None, "alice@example.com"), "password": (None, PASSWORD)}}, id="multipart"
    ),
    pytest.param(
        {"content": json.dumps(CREDENTIALS), "headers": {"Content-Type": "text/plain"}}, id="text-plain-json"
    ),
]


@pytest.mark.parametrize("body", FORM_BODIES)
@pytest.mark.parametrize("path", [LOGIN, REGISTER])
def test_cross_site_form_submission_is_rejected(
    alice: ApiClient, make_client, db: Session, path: str, body: dict
) -> None:
    sessions_before, users_before = count(db, UserSession), count(db, User)
    headers = {"Origin": EVIL, **body.pop("headers", {})}
    response = make_client().post(path, headers=headers, **body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "untrusted_origin"
    assert "tf_session" not in response.cookies
    assert (count(db, UserSession), count(db, User)) == (sessions_before, users_before)


@pytest.mark.parametrize("body", FORM_BODIES)
@pytest.mark.parametrize("origin", [None, TRUSTED_ORIGIN])
def test_form_bodies_are_rejected_even_without_an_untrusted_origin(
    alice: ApiClient, make_client, db: Session, body: dict, origin: str | None
) -> None:
    headers = {**({"Origin": origin} if origin else {}), **body.pop("headers", {})}
    response = make_client().post(LOGIN, headers=headers, **body)
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert count(db, UserSession) == 1  # only alice's registration session


def test_json_with_charset_parameter_is_accepted(alice: ApiClient, make_client) -> None:
    response = make_client().post(
        LOGIN, content=json.dumps(CREDENTIALS), headers={"Content-Type": "application/json; charset=utf-8"}
    )
    assert response.status_code == 200


# --- Failed-login rate limiting -----------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(app: FastAPI) -> FakeClock:
    fake = FakeClock()
    app.state.login_limiter.set_clock(fake)
    return fake


def fail_login(client: ApiClient, email: str = "alice@example.com") -> None:
    response = client.post(LOGIN, json={"email": email, "password": "definitely wrong"})
    assert response.status_code == 401, response.text


def test_failures_beyond_limit_are_throttled_even_with_correct_password(
    alice: ApiClient, make_client, db: Session, clock: FakeClock
) -> None:
    attacker = make_client()
    for _ in range(LOGIN_MAX_FAILURES):
        fail_login(attacker)
    sessions_before = count(db, UserSession)

    response = attacker.post(LOGIN, json=CREDENTIALS)
    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "too_many_attempts"
    assert response.headers["retry-after"] == str(LOGIN_WINDOW_SECONDS)
    assert body["details"] == {"retry_after_seconds": LOGIN_WINDOW_SECONDS}
    assert PASSWORD not in response.text
    assert count(db, UserSession) == sessions_before


def test_retry_after_counts_down(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES):
        fail_login(client)
    clock.now += 20
    assert client.post(LOGIN, json=CREDENTIALS).headers["retry-after"] == str(LOGIN_WINDOW_SECONDS - 20)


def test_login_succeeds_after_window_resets(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES):
        fail_login(client)
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 429

    clock.now += LOGIN_WINDOW_SECONDS - 1
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 429
    clock.now += 1
    response = client.post(LOGIN, json=CREDENTIALS)
    assert response.status_code == 200
    client.remember_auth(response.json())
    assert client.get("/api/v1/auth/me").status_code == 200


def test_successful_login_resets_the_account_budget(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES - 1):
        fail_login(client)
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 200
    for _ in range(LOGIN_MAX_FAILURES - 1):
        fail_login(client)  # would be 429 if the earlier failures still counted
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 200


def test_unknown_emails_are_throttled_identically(make_client, clock: FakeClock) -> None:
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES):
        fail_login(client, "ghost@example.com")
    response = client.post(LOGIN, json={"email": "ghost@example.com", "password": "x"})
    assert response.status_code == 429


def test_email_normalization_applies_to_the_limiter(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    for variant in ("alice@example.com", "ALICE@example.com", " Alice@Example.COM "):
        fail_login(client, variant)
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 429


def test_throttling_is_per_client_ip(alice: ApiClient, make_client, clock: FakeClock) -> None:
    attacker = make_client("203.0.113.9")
    for _ in range(LOGIN_MAX_FAILURES):
        fail_login(attacker)
    assert attacker.post(LOGIN, json=CREDENTIALS).status_code == 429
    # The legitimate user on another address is not locked out (no account lockout).
    assert make_client("198.51.100.7").post(LOGIN, json=CREDENTIALS).status_code == 200


def test_per_ip_budget_caps_spraying_across_emails(alice: ApiClient, make_client, clock: FakeClock) -> None:
    sprayer = make_client("203.0.113.10")
    for i in range(LOGIN_MAX_FAILURES_PER_IP):
        fail_login(sprayer, f"victim{i}@example.com")  # 1 failure per email: per-account budget never trips
    assert sprayer.post(LOGIN, json=CREDENTIALS).status_code == 429
    assert make_client("198.51.100.8").post(LOGIN, json=CREDENTIALS).status_code == 200


def test_retry_after_is_exposed_to_the_browser_origin(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    browser = {"Origin": TRUSTED_ORIGIN}
    for _ in range(LOGIN_MAX_FAILURES):
        client.post(LOGIN, json={"email": "alice@example.com", "password": "nope"}, headers=browser)
    response = client.post(LOGIN, json=CREDENTIALS, headers=browser)
    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    exposed = {h.strip().lower() for h in response.headers["access-control-expose-headers"].split(",")}
    assert {"retry-after", "x-request-id"} <= exposed
    assert response.headers["retry-after"] == str(LOGIN_WINDOW_SECONDS)


def test_untrusted_origin_gets_no_cors_grant(make_client) -> None:
    # Starlette sends Expose-Headers/Allow-Credentials on every response; without Allow-Origin the browser
    # ignores them and withholds the whole response from the untrusted page.
    response = make_client().post(LOGIN, json=CREDENTIALS, headers={"Origin": EVIL})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


@pytest.fixture
def tiny_limiter(app: FastAPI) -> Iterator[LoginRateLimiter]:
    """A limiter whose budgets can track only two keys each, swapped into the running app.

    Swapped by hand: pytest's monkeypatch reads the old value from ``__dict__``, but Starlette's State keeps
    attributes elsewhere, so ``undo()`` would delete ``login_limiter`` instead of restoring it."""
    original = app.state.login_limiter
    app.state.login_limiter = LoginRateLimiter.build(
        max_failures=LOGIN_MAX_FAILURES,
        max_failures_per_ip=LOGIN_MAX_FAILURES_PER_IP,
        window_seconds=LOGIN_WINDOW_SECONDS,
        max_keys=2,
    )
    try:
        yield app.state.login_limiter
    finally:
        app.state.login_limiter = original


def test_login_is_refused_with_429_when_every_tracked_key_holds_a_live_reservation(
    alice: ApiClient, make_client, tiny_limiter: LoginRateLimiter, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two in-flight logins from this client (other emails) fill the per-account table.
    held = [tiny_limiter.reserve("testclient", f"inflight{i}@example.com") for i in range(2)]
    calls: list[str] = []
    monkeypatch.setattr(auth_routes, "verify_password", lambda *a: calls.append("verify") or False)

    response = make_client().post(LOGIN, json=CREDENTIALS)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_attempts"
    assert response.headers["retry-after"] == "1"
    assert calls == []  # refused before any password work
    # Nothing was reserved for alice in either budget, and the in-flight reservations are intact.
    assert tiny_limiter.per_account.pending_count(tiny_limiter.account_key("testclient", "alice@example.com")) == 0
    assert tiny_limiter.per_ip.pending_count("testclient") == 2
    for attempt in held:
        assert tiny_limiter.per_account.pending_count(attempt.account_key) == 1

    # Once the in-flight logins finish, alice can log in (their idle entries are now evictable).
    for attempt in held:
        attempt.fail()
    monkeypatch.undo()
    assert make_client().post(LOGIN, json=CREDENTIALS).status_code == 200


def test_full_ip_table_refuses_new_client_without_partial_account_reservation(
    alice: ApiClient, make_client, tiny_limiter: LoginRateLimiter
) -> None:
    held = [tiny_limiter.reserve(ip, "alice@example.com") for ip in ("198.51.100.1", "198.51.100.2")]
    tiny_limiter.per_account.max_keys = 10  # room in the account budget; only the IP budget is full
    response = make_client("203.0.113.50").post(LOGIN, json=CREDENTIALS)
    assert response.status_code == 429 and response.headers["retry-after"] == "1"
    assert tiny_limiter.per_account.pending_count(tiny_limiter.account_key("203.0.113.50", "alice@example.com")) == 0
    assert len(tiny_limiter.per_account) == 2 and len(tiny_limiter.per_ip) == 2
    for attempt in held:
        attempt.release()


def test_rejected_requests_do_not_consume_the_budget(alice: ApiClient, make_client, clock: FakeClock) -> None:
    client = make_client()
    for _ in range(LOGIN_MAX_FAILURES * 2):
        assert client.post(LOGIN, json={"email": "alice@example.com"}).status_code == 422
        assert client.post(LOGIN, json=CREDENTIALS, headers={"Origin": EVIL}).status_code == 403
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 200
