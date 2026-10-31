from datetime import timedelta

from fastapi import APIRouter, Request, Response, status
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
from app.core.config import Settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimited
from app.core.security import csrf_token_for, hash_password, new_session_token, sha256_hex, verify_password
from app.db.base import utcnow
from app.db.errors import constraint_name
from app.models import User, UserSession
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserOut

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
        403: {"description": "Untrusted Origin"},
        409: {"description": "Email already registered"},
        415: {"description": "Body is not application/json"},
    },
)
def register(body: RegisterRequest, response: Response, db: DbSession, settings: AppSettings) -> AuthResponse:
    user = User(email=body.email, password_hash=hash_password(body.password), display_name=body.display_name)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if constraint_name(exc) == "uq_users_email":
            raise AppError(409, "email_taken", "An account with this email already exists.") from None
        raise
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
    # The direct ASGI peer address. Forwarded headers are never consulted (see app/serve.py).
    client_ip = request.client.host if request.client else "unknown"
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
