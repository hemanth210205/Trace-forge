"""Sign in with Google / GitHub. The providers are replaced by a fake ``fetch``; nothing leaves the machine."""

import base64
import hashlib
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.models import OAuthIdentity, User
from app.services import oauth
from tests.conftest import PASSWORD, ApiClient

PUBLIC = "http://testserver"
ME = "/api/v1/auth/me"


class FakeProviders:
    """Records requests and answers like Google's and GitHub's token, userinfo and email endpoints."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str], dict[str, str] | None]] = []
        self.token_status = 200
        self.google_user: dict[str, Any] = {"sub": "g-123", "email": "Ada@Example.com", "email_verified": True, "name": "Ada Lovelace"}
        self.github_user: dict[str, Any] = {"id": 4242, "login": "ada", "name": "Ada L."}
        self.github_emails: list[dict[str, Any]] = [
            {"email": "other@example.com", "verified": True, "primary": False},
            {"email": "ada@example.com", "verified": True, "primary": True},
        ]

    def __call__(self, method: str, url: str, headers: dict[str, str], form: dict[str, str] | None) -> tuple[int, Any]:
        self.calls.append((method, url, headers, form))
        if url in (oauth.PROVIDERS["google"].token_url, oauth.PROVIDERS["github"].token_url):
            return self.token_status, {"access_token": "provider-token"} if self.token_status == 200 else {"error": "bad_code"}
        assert headers.get("Authorization") == "Bearer provider-token"
        if url.endswith("/v1/userinfo"):
            return 200, self.google_user
        if url == "https://api.github.com/user":
            return 200, self.github_user
        if url == "https://api.github.com/user/emails":
            return 200, self.github_emails
        raise AssertionError(f"unexpected request {method} {url}")

    def token_form(self) -> dict[str, str]:
        return next(form for _, url, _, form in self.calls if url.endswith("token") and form is not None)


@pytest.fixture
def providers(app: FastAPI, settings: Settings) -> Iterator[FakeProviders]:
    fake = FakeProviders()
    original_settings, original_fetch = app.state.settings, app.state.oauth_fetch
    app.state.settings = settings.model_copy(
        update={
            "public_url": PUBLIC,
            "google_client_id": "google-id",
            "google_client_secret": SecretStr("google-secret"),
            "github_client_id": "github-id",
            "github_client_secret": SecretStr("github-secret"),
        }
    )
    app.state.oauth_fetch = fake
    yield fake
    app.state.settings, app.state.oauth_fetch = original_settings, original_fetch


def start(client: ApiClient, provider: str, next_path: str | None = None) -> dict[str, list[str]]:
    params = {"next": next_path} if next_path else {}
    response = client.get(f"/api/v1/auth/oauth/{provider}/start", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    return parse_qs(urlsplit(response.headers["location"]).query)


def callback(client: ApiClient, provider: str, **params: str) -> str:
    response = client.get(f"/api/v1/auth/oauth/{provider}/callback", params=params, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def sign_in(client: ApiClient, provider: str, next_path: str | None = None) -> str:
    query = start(client, provider, next_path)
    return callback(client, provider, code="the-code", state=query["state"][0])


# --- configuration -------------------------------------------------------------------------------------------


def test_no_provider_is_offered_without_credentials(client: ApiClient) -> None:
    assert client.get("/api/v1/auth/providers").json() == {"providers": []}
    response = client.get("/api/v1/auth/oauth/github/start", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("/login?oauth_error=not_configured")


def test_configured_providers_are_listed(client: ApiClient, providers: FakeProviders) -> None:
    assert client.get("/api/v1/auth/providers").json() == {"providers": ["google", "github"]}


def test_unknown_provider_is_not_configured(client: ApiClient, providers: FakeProviders) -> None:
    response = client.get("/api/v1/auth/oauth/facebook/start", follow_redirects=False)
    assert response.headers["location"] == f"{PUBLIC}/login?oauth_error=not_configured"


# --- start -----------------------------------------------------------------------------------------------------


def test_github_start_redirects_with_state_and_sets_a_short_lived_cookie(client: ApiClient, providers: FakeProviders) -> None:
    response = client.get("/api/v1/auth/oauth/github/start", follow_redirects=False)
    location = urlsplit(response.headers["location"])
    query = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://github.com/login/oauth/authorize"
    assert query["client_id"] == ["github-id"]
    assert query["redirect_uri"] == [f"{PUBLIC}/api/v1/auth/oauth/github/callback"]
    assert query["scope"] == ["read:user user:email"]
    assert len(query["state"][0]) >= 40
    assert "code_challenge" not in query
    cookie = response.headers["set-cookie"]
    assert "tf_oauth=" in cookie and "HttpOnly" in cookie and "Max-Age=600" in cookie
    assert "Path=/api/v1/auth/oauth" in cookie and "SameSite=lax" in cookie
    assert "github-secret" not in response.headers["location"] + cookie


def test_google_start_uses_pkce(client: ApiClient, providers: FakeProviders) -> None:
    query = start(client, "google")
    assert query["code_challenge_method"] == ["S256"]
    assert query["prompt"] == ["select_account"]
    callback(client, "google", code="the-code", state=query["state"][0])
    verifier = providers.token_form()["code_verifier"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == [expected]


# --- callback --------------------------------------------------------------------------------------------------


def test_github_sign_in_creates_a_passwordless_account_and_session(client: ApiClient, providers: FakeProviders, db: Session) -> None:
    location = sign_in(client, "github", next_path="/projects")
    assert location == f"{PUBLIC}/projects"
    form = providers.token_form()
    assert form["code"] == "the-code" and form["client_secret"] == "github-secret"
    assert form["redirect_uri"] == f"{PUBLIC}/api/v1/auth/oauth/github/callback"

    me = client.get(ME)
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ada@example.com"  # the primary verified address
    assert me.json()["user"]["display_name"] == "Ada L."
    user = db.scalar(select(User))
    assert user is not None and user.password_hash is None
    identity = db.scalar(select(OAuthIdentity))
    assert identity is not None and (identity.provider, identity.subject) == ("github", "4242")
    assert "tf_oauth" not in client.cookies


def test_google_sign_in_lowercases_the_email(client: ApiClient, providers: FakeProviders) -> None:
    sign_in(client, "google")
    assert client.get(ME).json()["user"]["email"] == "ada@example.com"


def test_returning_user_is_found_by_provider_id_even_if_the_email_changed(
    make_client: Any, providers: FakeProviders, db: Session
) -> None:
    sign_in(make_client(), "github")
    providers.github_emails = [{"email": "ada.new@example.com", "verified": True, "primary": True}]
    second = make_client()
    sign_in(second, "github")
    assert second.get(ME).json()["user"]["email"] == "ada@example.com"
    assert len(db.scalars(select(User)).all()) == 1


def test_existing_password_account_is_linked_by_verified_email(make_client: Any, providers: FakeProviders, db: Session) -> None:
    owner = make_client()
    owner.register("ada@example.com", display_name="Ada")
    oauth_client = make_client()
    sign_in(oauth_client, "google")
    assert oauth_client.get(ME).json()["user"]["display_name"] == "Ada"
    assert len(db.scalars(select(User)).all()) == 1
    # The password still works for the linked account.
    login = make_client().post("/api/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD})
    assert login.status_code == 200


def test_passwordless_account_cannot_sign_in_with_a_password(make_client: Any, providers: FakeProviders) -> None:
    sign_in(make_client(), "github")
    response = make_client().post("/api/v1/auth/login", json={"email": "ada@example.com", "password": "anything at all"})
    assert response.status_code == 401


@pytest.mark.parametrize("provider", ["google", "github"])
def test_unverified_email_is_refused(client: ApiClient, providers: FakeProviders, db: Session, provider: str) -> None:
    providers.google_user["email_verified"] = False
    providers.github_emails = [{"email": "ada@example.com", "verified": False, "primary": True}]
    assert sign_in(client, provider) == f"{PUBLIC}/login?oauth_error=email_unverified"
    assert db.scalar(select(User)) is None
    assert client.get(ME).status_code == 401


def test_wrong_state_is_refused(client: ApiClient, providers: FakeProviders, db: Session) -> None:
    start(client, "github")
    assert callback(client, "github", code="the-code", state="forged") == f"{PUBLIC}/login?oauth_error=state_mismatch"
    assert providers.calls == []  # the code was never exchanged
    assert db.scalar(select(User)) is None


def test_missing_state_cookie_is_refused(make_client: Any, providers: FakeProviders) -> None:
    query = start(make_client(), "github")
    # A different browser (no cookie) replays the provider's redirect.
    location = callback(make_client(), "github", code="the-code", state=query["state"][0])
    assert location.endswith("oauth_error=state_mismatch")


def test_state_from_another_provider_is_refused(client: ApiClient, providers: FakeProviders) -> None:
    query = start(client, "google")
    assert callback(client, "github", code="the-code", state=query["state"][0]).endswith("oauth_error=state_mismatch")


def test_tampered_cookie_is_refused(client: ApiClient, providers: FakeProviders) -> None:
    query = start(client, "github")
    payload, _ = client.cookies["tf_oauth"].rsplit(".", 1)
    client.cookies.set("tf_oauth", f"{payload}.forged-signature", path="/api/v1/auth/oauth")
    assert callback(client, "github", code="the-code", state=query["state"][0]).endswith("oauth_error=state_mismatch")


def test_expired_state_is_refused(client: ApiClient, providers: FakeProviders, monkeypatch: pytest.MonkeyPatch) -> None:
    query = start(client, "github")
    later = time.time() + oauth.STATE_TTL_SECONDS + 1
    monkeypatch.setattr(oauth, "_now", lambda: later)
    assert callback(client, "github", code="the-code", state=query["state"][0]).endswith("oauth_error=state_expired")


def test_user_who_cancels_at_the_provider_is_sent_back(client: ApiClient, providers: FakeProviders) -> None:
    query = start(client, "github")
    assert callback(client, "github", error="access_denied", state=query["state"][0]).endswith("oauth_error=denied")


def test_failed_code_exchange_is_reported(client: ApiClient, providers: FakeProviders) -> None:
    providers.token_status = 400
    assert sign_in(client, "github").endswith("oauth_error=token_exchange_failed")


def test_inactive_account_cannot_sign_in(make_client: Any, providers: FakeProviders, db: Session) -> None:
    sign_in(make_client(), "github")
    db.execute(update(User).values(is_active=False))
    db.commit()
    client = make_client()
    assert sign_in(client, "github").endswith("oauth_error=inactive")
    assert client.get(ME).status_code == 401


@pytest.mark.parametrize("next_path", ["https://evil.example/", "//evil.example/", "/\\evil.example", "projects"])
def test_return_path_cannot_leave_the_console(client: ApiClient, providers: FakeProviders, next_path: str) -> None:
    assert sign_in(client, "github", next_path=next_path) == f"{PUBLIC}/"


# --- schema ----------------------------------------------------------------------------------------------------


def test_migrations_match_the_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_concurrent_creation_of_the_same_account_is_a_clean_retry(
    client: ApiClient, providers: FakeProviders, engine: Engine, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two callbacks for one new email: the second insert hits the unique email. It must not be a 500."""
    from app.services import invitations as invitations_service

    original = invitations_service.check_before_create

    def concurrent_signup(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        # Another request commits the same account right after this one found no account.
        with Session(engine) as other:
            other.add(User(email="ada@example.com", password_hash=None, display_name="First"))
            other.commit()

    monkeypatch.setattr(invitations_service, "check_before_create", concurrent_signup)
    assert sign_in(client, "github") == f"{PUBLIC}/login?oauth_error=try_again"
    assert client.get(ME).status_code == 401
    db.expire_all()
    assert db.scalar(select(OAuthIdentity)) is None  # nothing half-linked
    monkeypatch.setattr(invitations_service, "check_before_create", original)
    assert sign_in(client, "github") == f"{PUBLIC}/"  # the retry links to the account that now exists
