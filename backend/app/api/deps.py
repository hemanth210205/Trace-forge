"""Request dependencies: settings, console session auth, CSRF, and project API key auth."""

from dataclasses import dataclass
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import proxy
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import LoginRateLimiter
from app.core.security import constant_time_equals, csrf_token_for, hash_api_key, parse_api_key, sha256_hex
from app.db.base import utcnow
from app.db.session import get_db
from app.models import Project, ProjectApiKey, User, UserSession

SESSION_COOKIE = "tf_session"
CSRF_COOKIE = "tf_csrf"
CSRF_HEADER = "X-CSRF-Token"

session_cookie = APIKeyCookie(
    name=SESSION_COOKIE, auto_error=False, scheme_name="SessionCookie", description="Console session cookie set by login."
)
csrf_header = APIKeyHeader(
    name=CSRF_HEADER,
    auto_error=False,
    scheme_name="CsrfToken",
    description="Required on state-changing requests authenticated by the session cookie.",
)
api_key_bearer = HTTPBearer(
    auto_error=False, scheme_name="ProjectApiKey", description="Project API key: `Authorization: Bearer tf_<prefix>_<secret>`."
)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


def get_login_limiter(request: Request) -> LoginRateLimiter:
    return request.app.state.login_limiter


LoginLimiter = Annotated[LoginRateLimiter, Depends(get_login_limiter)]


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else None


def require_trusted_origin(request: Request, settings: AppSettings) -> None:
    """Login-CSRF defence for the unauthenticated endpoints that issue session cookies.

    - ``Origin`` present: it must be the server's own origin (the console it serves) or a configured trusted
      origin. ``null`` is never trusted.
    - ``Origin`` absent but ``Referer`` present: the Referer's origin must be trusted.
    - Both absent: allowed. Browsers send ``Origin`` on every cross-origin POST, so such a request comes
      from a non-browser client (curl, scripts, the test suite), which cannot ride on a victim's browser.

    Trusting the own origin is safe because ``TrustedHostMiddleware`` only admits local Host names: a
    DNS-rebinding page has its own host name in both ``Origin`` and ``Host`` and is rejected before this runs.
    """
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is None:
            return
        origin = _origin_of(referer)
    trusted = {o.lower() for o in settings.cors_origin_list}
    host = request.headers.get("host")
    if host:
        # Behind a trusted TLS-terminating proxy the browser's origin is https even though this hop is http.
        trusted.add(f"{proxy.request_scheme(request, settings.trusted_proxy_networks)}://{host}".lower())
    if origin is None or origin.rstrip("/").lower() not in trusted:
        raise AppError(403, "untrusted_origin", "Request origin is not allowed.")


def require_json_body(request: Request) -> None:
    """Rejects non-JSON bodies, which rules out every content type an HTML form can submit cross-site."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise AppError(415, "unsupported_media_type", "Request body must be application/json.")


# Order matters: the Origin check runs first so cross-site requests get 403 whatever their body.
COOKIE_ISSUING_GUARDS = [Depends(require_trusted_origin), Depends(require_json_body)]


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: UserSession
    token: str  # raw cookie token; needed to recompute the CSRF token, never persisted


def get_auth_context(db: DbSession, token: Annotated[str | None, Security(session_cookie)]) -> AuthContext:
    if not token:
        raise AppError(401, "not_authenticated", "Authentication required.")
    session = db.scalar(select(UserSession).where(UserSession.token_hash == sha256_hex(token)))
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= utcnow()
        or not session.user.is_active
    ):
        raise AppError(401, "not_authenticated", "Authentication required.")
    return AuthContext(user=session.user, session=session, token=token)


def require_csrf(
    ctx: Annotated[AuthContext, Depends(get_auth_context)],
    settings: AppSettings,
    header: Annotated[str | None, Security(csrf_header)],
) -> AuthContext:
    expected = csrf_token_for(ctx.token, settings.secret_key.get_secret_value())
    if not header or not constant_time_equals(header, expected):
        raise AppError(403, "csrf_failed", "Missing or invalid CSRF token.")
    return ctx


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]
# Use for every state-changing route authenticated by the session cookie.
CsrfAuth = Annotated[AuthContext, Depends(require_csrf)]


@dataclass(frozen=True)
class ApiKeyContext:
    api_key: ProjectApiKey
    project: Project


def get_api_key_context(
    db: DbSession,
    settings: AppSettings,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(api_key_bearer)],
) -> ApiKeyContext:
    if credentials is None:
        raise AppError(401, "api_key_required", "A project API key is required.")
    invalid = AppError(401, "invalid_api_key", "Invalid or revoked API key.")
    raw_key = credentials.credentials
    prefix = parse_api_key(raw_key)
    if prefix is None:
        raise invalid
    row = db.execute(
        select(ProjectApiKey, Project)
        .join(Project, Project.id == ProjectApiKey.project_id)
        .join(User, User.id == Project.owner_id)
        .where(ProjectApiKey.prefix == prefix, User.is_active.is_(True))
    ).first()
    computed = hash_api_key(raw_key, settings.api_key_pepper.get_secret_value())
    if row is None or not constant_time_equals(computed, row.ProjectApiKey.key_hash):
        raise invalid
    api_key, project = row.ProjectApiKey, row.Project
    if api_key.revoked_at is not None:
        raise invalid
    api_key.last_used_at = utcnow()
    db.commit()
    return ApiKeyContext(api_key=api_key, project=project)


CurrentApiKey = Annotated[ApiKeyContext, Depends(get_api_key_context)]
