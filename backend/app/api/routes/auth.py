from datetime import timedelta

from fastapi import APIRouter, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    COOKIE_ISSUING_GUARDS,
    CSRF_COOKIE,
    SESSION_COOKIE,
    AppSettings,
    CsrfAuth,
    CurrentAuth,
    DbSession,
    LoginLimiter,
)
from app.core import proxy
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimited
from app.core.security import csrf_token_for, hash_password, new_session_token, sha256_hex, verify_password
from app.db.base import utcnow
from app.db.errors import constraint_name
from app.models import OAuthIdentity, User, UserSession
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserOut, canonical_email
from app.services import invitations, oauth

router = APIRouter(prefix="/auth", tags=["auth"])


def _auth_response(user: User, token: str, settings: Settings) -> AuthResponse:
    return AuthResponse(
        user=UserOut.model_validate(user), csrf_token=csrf_token_for(token, settings.secret_key.get_secret_value())
    )


def _start_session(db: DbSession, user: User, settings: Settings, response: Response) -> str:
    """Creates a server-side session (storing only the token's SHA-256) and sets the session and CSRF cookies."""
    token = new_session_token()
    max_age = settings.session_ttl_hours * 3600
    db.add(UserSession(user_id=user.id, token_hash=sha256_hex(token), expires_at=utcnow() + timedelta(seconds=max_age)))
    common = {"max_age": max_age, "path": "/", "secure": settings.cookie_secure, "samesite": "lax"}
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    # Readable by the console so it can echo the value in the X-CSRF-Token header.
    response.set_cookie(CSRF_COOKIE, csrf_token_for(token, settings.secret_key.get_secret_value()), httponly=False, **common)
    response.headers["Cache-Control"] = "no-store"
    return token


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthResponse,
    summary="Create an account and start a session",
    dependencies=COOKIE_ISSUING_GUARDS,
    responses={
        403: {"description": "Untrusted Origin, or registration refused by TRACEFORGE_REGISTRATION"},
        409: {"description": "Email already registered"},
        415: {"description": "Body is not application/json"},
    },
)
def register(body: RegisterRequest, response: Response, db: DbSession, settings: AppSettings) -> AuthResponse:
    # Policy first: a closed server must not reveal which emails are registered (no 409 before the 403).
    try:
        invitations.check_before_create(settings, body.email, has_code=bool(body.invite_code), verified_email=False)
    except invitations.RegistrationRefused as exc:
        raise AppError(403, exc.code, exc.message) from None
    user = User(email=body.email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if constraint_name(exc) == "uq_users_email":
            raise AppError(409, "email_taken", "An account with this email already exists.") from None
        raise
    try:
        # Same transaction as the new account: both are committed together or not at all.
        invitations.consume_for_new_account(db, settings, user, code=body.invite_code, verified_email=False)
    except invitations.RegistrationRefused as exc:
        db.rollback()
        raise AppError(403, exc.code, exc.message) from None
    token = _start_session(db, user, settings, response)
    db.commit()
    return _auth_response(user, token, settings)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Start a session",
    dependencies=COOKIE_ISSUING_GUARDS,
    responses={
        401: {"description": "Invalid credentials"},
        403: {"description": "Untrusted Origin"},
        415: {"description": "Body is not application/json"},
        429: {"description": "Too many failed attempts; see the Retry-After header"},
    },
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: AppSettings,
    limiter: LoginLimiter,
) -> AuthResponse:
    # The direct peer, or the client behind a configured trusted proxy (app/core/proxy.py). Forwarded headers from
    # anyone else are ignored, so a client cannot pick its own address and get a fresh rate-limit budget.
    client_ip = proxy.client_ip(request, settings.trusted_proxy_networks)
    # Reserved atomically in both budgets before any password work, so concurrent requests cannot overshoot
    # the budget and a throttled client cannot keep the argon2 hasher busy.
    try:
        attempt = limiter.reserve(client_ip, body.email)
    except RateLimited as exc:
        raise AppError(
            429,
            "too_many_attempts",
            "Too many failed login attempts. Try again later.",
            details={"retry_after_seconds": exc.retry_after},
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    with attempt:  # released if anything below raises before the attempt is finalized
        user = db.scalar(select(User).where(User.email == body.email))
        # verify_password checks a dummy hash when the user is missing, so timing does not reveal registered emails.
        password_ok = verify_password(user.password_hash if user else None, body.password)
        if user is None or not password_ok or not user.is_active:
            attempt.fail()
            raise AppError(401, "invalid_credentials", "Invalid email or password.")
        attempt.succeed()
    token = _start_session(db, user, settings, response)
    db.commit()
    return _auth_response(user, token, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke the current session")
def logout(ctx: CsrfAuth, db: DbSession, settings: AppSettings) -> Response:
    ctx.session.revoked_at = utcnow()
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=settings.cookie_secure, samesite="lax")
    return response


@router.get("/me", response_model=AuthResponse, summary="Current user")
def me(ctx: CurrentAuth, response: Response, settings: AppSettings) -> AuthResponse:
    response.headers["Cache-Control"] = "no-store"
    return _auth_response(ctx.user, ctx.token, settings)


# --- Sign in with Google / GitHub (see app/services/oauth.py) -------------------------------------------------

OAUTH_COOKIE_PATH = "/api/v1/auth/oauth"


@router.get("/providers", summary="Sign-in providers configured on this server")
def providers(settings: AppSettings) -> dict[str, list[str]]:
    return {"providers": settings.oauth_providers}


@router.get("/registration", summary="Who may create an account on this server")
def registration(settings: AppSettings) -> dict[str, str]:
    """``open``, ``disabled``, ``domain`` or ``invite``; the console adapts the sign-up form. Domains are not listed."""
    return {"mode": settings.registration}


def _login_redirect(settings: Settings, code: str) -> RedirectResponse:
    response = RedirectResponse(f"{settings.public_origin}/login?oauth_error={code}", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(oauth.STATE_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


@router.get("/oauth/{provider}/start", summary="Start signing in with a provider (browser redirect)", include_in_schema=True)
def oauth_start(provider: str, settings: AppSettings, next: str | None = None) -> RedirectResponse:
    try:
        spec = oauth.provider_for(provider, settings)
    except oauth.OAuthError as exc:
        return _login_redirect(settings, exc.code)
    cookie, state, challenge = oauth.new_state(spec, oauth.safe_next(next), settings)
    response = RedirectResponse(oauth.authorize_location(spec, settings, state, challenge), status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        oauth.STATE_COOKIE, cookie, max_age=oauth.STATE_TTL_SECONDS, path=OAUTH_COOKIE_PATH,
        httponly=True, secure=settings.cookie_secure, samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/oauth/{provider}/callback", summary="Provider callback: finish signing in (browser redirect)")
def oauth_callback(
    provider: str,
    request: Request,
    db: DbSession,
    settings: AppSettings,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    try:
        spec = oauth.provider_for(provider, settings)
        if error:
            raise oauth.OAuthError("denied" if error == "access_denied" else "provider_error")
        stored = oauth.read_state(request.cookies.get(oauth.STATE_COOKIE), spec, state, settings)
        if not code:
            raise oauth.OAuthError("provider_error")
        profile = oauth.fetch_profile(spec, code, stored.get("v"), settings, request.app.state.oauth_fetch)
        user = _user_for_profile(db, settings, spec.name, profile)
        if not user.is_active:
            raise oauth.OAuthError("inactive")
    except oauth.OAuthError as exc:
        db.rollback()
        return _login_redirect(settings, exc.code)

    response = RedirectResponse(f"{settings.public_origin}{oauth.safe_next(stored.get('n'))}", status_code=status.HTTP_303_SEE_OTHER)
    _start_session(db, user, settings, response)
    db.commit()
    response.delete_cookie(oauth.STATE_COOKIE, path=OAUTH_COOKIE_PATH)
    return response


def _user_for_profile(db: DbSession, settings: Settings, provider: str, profile: oauth.Profile) -> User:
    """The linked user; else the account with this verified email (linked now); else a new password-less account."""
    identity = db.scalar(select(OAuthIdentity).where(OAuthIdentity.provider == provider, OAuthIdentity.subject == profile.subject))
    if identity is not None:
        user = db.get(User, identity.user_id)
        assert user is not None
        return user
    # The same canonical form registration stores, so both paths agree on one address.
    email = canonical_email(profile.email)
    if not email or len(email) > 320 or "@" not in email:
        raise oauth.OAuthError("email_unverified")
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # A new account: the registration policy applies exactly as for the password form.
        try:
            invitations.check_before_create(settings, email, has_code=False, verified_email=True)
        except invitations.RegistrationRefused as exc:
            raise oauth.OAuthError(exc.code) from None
        name = (profile.name.strip() or email.split("@", 1)[0])[:100]
        user = User(email=email, password_hash=None, display_name=name)
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            # A concurrent sign-in created this account between the lookup above and this insert.
            db.rollback()
            raise oauth.OAuthError("try_again") from None
        try:
            invitations.consume_for_new_account(db, settings, user, code=None, verified_email=True)
        except invitations.RegistrationRefused as exc:
            raise oauth.OAuthError(exc.code) from None
    db.add(OAuthIdentity(user_id=user.id, provider=provider, subject=profile.subject, email=email))
    try:
        db.flush()
    except IntegrityError:
        # A concurrent callback linked the same provider account first.
        db.rollback()
        raise oauth.OAuthError("try_again") from None
    return user
