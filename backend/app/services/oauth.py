"""Sign in with Google or GitHub (OAuth 2.0 authorization-code flow).

Flow: ``/auth/oauth/<provider>/start`` stores a random ``state`` (and, for Google, a PKCE verifier) in a short-lived
signed cookie and redirects to the provider. The provider redirects back to ``/callback`` with a code; the server
checks the state against the cookie, exchanges the code for an access token, reads the user's *verified* email and
stable id, and starts a normal console session.

Security notes:
- ``state`` is compared in constant time against the signed cookie, which is HttpOnly, SameSite=Lax and lives
  10 minutes. Google additionally uses PKCE (S256).
- Only provider-verified emails are accepted. An account is found by its provider id first; an existing account
  with the same (verified) email is linked once, after which the provider id is what matters.
- The return path after sign-in must be a local path, so the flow cannot be used as an open redirect.
- Provider HTTP calls go through a ``Fetch`` function (stdlib ``urllib``), replaceable in tests.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings

STATE_COOKIE = "tf_oauth"
STATE_TTL_SECONDS = 600
HTTP_TIMEOUT_SECONDS = 10


def _now() -> float:
    """Wall clock for state expiry (replaceable in tests)."""
    return time.time()

# fetch(method, url, headers, form) -> (status, parsed JSON or None)
Fetch = Callable[[str, str, dict[str, str], dict[str, str] | None], tuple[int, Any]]


class OAuthError(Exception):
    """Ends the flow; ``code`` is shown to the user as ``/login?oauth_error=<code>``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Provider:
    name: str
    authorize_url: str
    token_url: str
    scope: str
    pkce: bool


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scope="openid email profile",
        pkce=True,
    ),
    "github": Provider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scope="read:user user:email",
        pkce=False,
    ),
}


@dataclass(frozen=True)
class Profile:
    subject: str
    email: str
    name: str


def urllib_fetch(method: str, url: str, headers: dict[str, str], form: dict[str, str] | None) -> tuple[int, Any]:
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Accept": "application/json", "User-Agent": "Traceforge", **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # noqa: S310 (fixed https URLs)
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise OAuthError("provider_unreachable") from None
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, None


# --- configuration -------------------------------------------------------------------------------------------


def provider_for(name: str, settings: Settings) -> Provider:
    if name not in PROVIDERS or name not in settings.oauth_providers:
        raise OAuthError("not_configured")
    return PROVIDERS[name]


def client_credentials(provider: Provider, settings: Settings) -> tuple[str, str]:
    if provider.name == "google":
        assert settings.google_client_id and settings.google_client_secret
        return settings.google_client_id, settings.google_client_secret.get_secret_value()
    assert settings.github_client_id and settings.github_client_secret
    return settings.github_client_id, settings.github_client_secret.get_secret_value()


def redirect_uri(provider: Provider, settings: Settings) -> str:
    return f"{settings.public_origin}/api/v1/auth/oauth/{provider.name}/callback"


def safe_next(value: str | None) -> str:
    """A local path only: '/projects/x' yes; 'https://evil', '//evil', '/\\evil' no."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or any(c in value for c in "\r\n"):
        return "/"
    return value[:500]


# --- signed state cookie -------------------------------------------------------------------------------------


def _sign(payload: str, settings: Settings) -> str:
    key = hashlib.sha256(b"traceforge-oauth-state:" + settings.secret_key.get_secret_value().encode()).digest()
    return base64.urlsafe_b64encode(hmac.new(key, payload.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def new_state(provider: Provider, next_path: str, settings: Settings) -> tuple[str, str, str | None]:
    """Returns (cookie value, state, PKCE challenge or None)."""
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48) if provider.pkce else None
    body = {"p": provider.name, "s": state, "v": verifier, "n": next_path, "e": int(_now()) + STATE_TTL_SECONDS}
    payload = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).decode().rstrip("=")
    challenge = None
    if verifier:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return f"{payload}.{_sign(payload, settings)}", state, challenge


def read_state(cookie: str | None, provider: Provider, state: str | None, settings: Settings) -> dict[str, Any]:
    """Validates the cookie against the provider and the returned ``state``; returns its contents."""
    if not cookie or not state or "." not in cookie:
        raise OAuthError("state_mismatch")
    payload, signature = cookie.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload, settings)):
        raise OAuthError("state_mismatch")
    try:
        body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        raise OAuthError("state_mismatch") from None
    if body.get("p") != provider.name or not hmac.compare_digest(str(body.get("s", "")), state):
        raise OAuthError("state_mismatch")
    if int(body.get("e", 0)) < _now():
        raise OAuthError("state_expired")
    return body


def authorize_location(provider: Provider, settings: Settings, state: str, challenge: str | None) -> str:
    client_id, _ = client_credentials(provider, settings)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(provider, settings),
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
    }
    if provider.name == "google":
        params["prompt"] = "select_account"
    if challenge:
        params.update(code_challenge=challenge, code_challenge_method="S256")
    return f"{provider.authorize_url}?{urllib.parse.urlencode(params)}"


# --- code exchange and profile -------------------------------------------------------------------------------


def fetch_profile(provider: Provider, code: str, verifier: str | None, settings: Settings, fetch: Fetch) -> Profile:
    client_id, client_secret = client_credentials(provider, settings)
    form = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri(provider, settings),
        "grant_type": "authorization_code",
    }
    if verifier:
        form["code_verifier"] = verifier
    status, token = fetch("POST", provider.token_url, {}, form)
    access_token = token.get("access_token") if isinstance(token, dict) else None
    if status != 200 or not isinstance(access_token, str) or not access_token:
        raise OAuthError("token_exchange_failed")
    auth = {"Authorization": f"Bearer {access_token}"}
    return _google_profile(auth, fetch) if provider.name == "google" else _github_profile(auth, fetch)


def _google_profile(auth: dict[str, str], fetch: Fetch) -> Profile:
    status, info = fetch("GET", "https://openidconnect.googleapis.com/v1/userinfo", auth, None)
    if status != 200 or not isinstance(info, dict) or not info.get("sub"):
        raise OAuthError("profile_failed")
    email = info.get("email")
    if not isinstance(email, str) or info.get("email_verified") is not True:
        raise OAuthError("email_unverified")
    return Profile(subject=str(info["sub"]), email=email, name=str(info.get("name") or ""))


def _github_profile(auth: dict[str, str], fetch: Fetch) -> Profile:
    headers = {**auth, "X-GitHub-Api-Version": "2022-11-28"}
    status, user = fetch("GET", "https://api.github.com/user", headers, None)
    if status != 200 or not isinstance(user, dict) or user.get("id") is None:
        raise OAuthError("profile_failed")
    status, emails = fetch("GET", "https://api.github.com/user/emails", headers, None)
    if status != 200 or not isinstance(emails, list):
        raise OAuthError("profile_failed")
    verified = [e for e in emails if isinstance(e, dict) and e.get("verified") is True and isinstance(e.get("email"), str)]
    chosen = next((e for e in verified if e.get("primary") is True), verified[0] if verified else None)
    if chosen is None:
        raise OAuthError("email_unverified")
    return Profile(subject=str(user["id"]), email=chosen["email"], name=str(user.get("name") or user.get("login") or ""))
