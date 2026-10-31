"""Application factory. Run with ``python -m app.serve`` (see that module for why not plain ``uvicorn``)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.api.routes import api_keys, auth, datasets, diagnoses, evaluations, experiments, health, ingest, projects, traces
from app.core.config import Settings, get_settings
from app.core.database import choice_from_environment, database_name, settings_for
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import BodySizeLimitMiddleware, RequestContextMiddleware, UnhandledErrorMiddleware
from app.core.rate_limit import LoginRateLimiter
from app.db.schema import ensure_current
from app.db.session import make_engine, make_sessionmaker
from app.frontend import frontend_route
from app.services.recovery import RecoveryWorker

API_PREFIX = "/api/v1"

DESCRIPTION = """
Traceforge API — console authentication, projects and project-scoped API keys.

**Console authentication** uses an HttpOnly `tf_session` cookie set by `/auth/register` or `/auth/login`.
Every state-changing request authenticated by that cookie must also send the `X-CSRF-Token` header with the
`csrf_token` returned by login (also available in the `tf_csrf` cookie).

**Project API keys** (`Authorization: Bearer tf_<prefix>_<secret>`) authenticate programmatic clients. A key's
secret is shown once, at creation; the server stores only an HMAC-SHA256 of it. **API keys do not expire**:
a key stays valid until its owner revokes it (`DELETE /projects/{project_id}/api-keys/{key_id}`) or the project
is deleted.

**Login and registration** accept only `application/json` bodies and reject requests whose `Origin` (or, when
absent, `Referer`) is neither the server's own origin nor a configured trusted origin. Repeated failed logins are throttled with `429` and a
`Retry-After` header.

Resources owned by another user respond with **404**, never 403.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    """``settings`` given (tests): used as-is. Otherwise the database is the guarded choice from the process
    environment (``TRACEFORGE_DATABASE``, default ``dev``); the main database is never a default."""
    settings = settings or settings_for(get_settings(), choice_from_environment())
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Never serve a schema the code does not match (e.g. a database left at an old revision).
        ensure_current(engine, database_name(settings.database_url.get_secret_value()))
        # Work interrupted by a previous crash is marked failed at startup and then periodically.
        worker = RecoveryWorker(app.state.sessionmaker, settings)
        worker.start()
        yield
        worker.stop()
        engine.dispose()

    app = FastAPI(
        title="Traceforge API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.login_limiter = LoginRateLimiter.build(
        max_failures=settings.login_max_failures,
        max_failures_per_ip=settings.login_max_failures_per_ip,
        window_seconds=settings.login_failure_window_seconds,
        max_keys=settings.login_limiter_max_keys,
    )

    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(auth.router)
    api.include_router(projects.router)
    api.include_router(api_keys.router)
    api.include_router(api_keys.key_auth_router)
    api.include_router(ingest.router)
    api.include_router(traces.router)
    api.include_router(datasets.router)
    api.include_router(evaluations.router)
    api.include_router(evaluations.sdk_router)
    api.include_router(diagnoses.router)
    api.include_router(experiments.router)
    api.include_router(experiments.sdk_router)
    app.include_router(api)
    console = frontend_route(Path(settings.frontend_dist)) if settings.frontend_dist else None
    if console is not None:
        app.router.routes.append(console)  # last: API routes and /api/docs always win

    # Added last = outermost. Outside in: request context (request id, access log) -> trusted host -> CORS ->
    # body size limit -> unhandled-error envelope -> routing. Keeping CORS outside the size limit and the 500 fallback means 413 and
    # 500 responses carry CORS and X-Request-ID headers too, so the console can read them.
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-ID"],
        # Retry-After is not CORS-safelisted; without this the console cannot read it from a 429.
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)
    app.add_middleware(RequestContextMiddleware)
    return app
