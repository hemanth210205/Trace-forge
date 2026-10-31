import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AppSettings, CsrfAuth, CurrentApiKey, CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.core.errors import AppError, NotFound
from app.core.security import generate_api_key, hash_api_key
from app.db.base import utcnow
from app.db.errors import constraint_name
from app.models import ProjectApiKey
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyIdentity, ApiKeyOut
from app.services.audit import record_audit

router = APIRouter(prefix="/projects/{project_id}/api-keys", tags=["api-keys"])
key_auth_router = APIRouter(prefix="/api-keys", tags=["api-keys"])

_NOT_FOUND = {404: {"description": "Project or key does not exist or is not owned by the caller"}}
_PREFIX_ATTEMPTS = 5


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiKeyCreated, responses=_NOT_FOUND)
def create_api_key(
    project_id: uuid.UUID, body: ApiKeyCreate, ctx: CsrfAuth, db: DbSession, settings: AppSettings, response: Response
) -> ApiKeyCreated:
    """Creates a key. The full key is in this response only; the server keeps just an HMAC-SHA256 of it.

    Keys do not expire. They stay valid until revoked with `DELETE .../api-keys/{key_id}` or until the project is deleted.
    """
    project = get_owned_project(db, ctx.user.id, project_id)
    pepper = settings.api_key_pepper.get_secret_value()
    for _ in range(_PREFIX_ATTEMPTS):
        generated = generate_api_key()
        api_key = ProjectApiKey(
            project_id=project.id,
            name=body.name,
            prefix=generated.prefix,
            key_hash=hash_api_key(generated.secret, pepper),
            created_by=ctx.user.id,
        )
        try:
            with db.begin_nested():
                db.add(api_key)
                db.flush()
            break
        except IntegrityError as exc:
            if constraint_name(exc) != "uq_project_api_keys_prefix":
                raise
    else:
        raise AppError(503, "key_generation_failed", "Could not allocate a unique key prefix. Try again.")

    record_audit(
        db,
        action="api_key.created",
        target_type="api_key",
        target_id=api_key.id,
        actor_user_id=ctx.user.id,
        project_id=project.id,
        details={"name": api_key.name, "prefix": api_key.prefix},
    )
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return ApiKeyCreated(**ApiKeyOut.model_validate(api_key).model_dump(), key=generated.secret)


@router.get("", response_model=list[ApiKeyOut], responses=_NOT_FOUND)
def list_api_keys(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> list[ProjectApiKey]:
    project = get_owned_project(db, ctx.user.id, project_id)
    return list(
        db.scalars(
            select(ProjectApiKey).where(ProjectApiKey.project_id == project.id).order_by(ProjectApiKey.created_at.desc())
        )
    )


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def revoke_api_key(project_id: uuid.UUID, key_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> Response:
    """Revokes a key immediately. Idempotent; only the first revocation is audited."""
    project = get_owned_project(db, ctx.user.id, project_id)
    api_key = db.scalar(select(ProjectApiKey).where(ProjectApiKey.id == key_id, ProjectApiKey.project_id == project.id))
    if api_key is None:
        raise NotFound("api_key")
    if api_key.revoked_at is None:
        api_key.revoked_at = utcnow()
        record_audit(
            db,
            action="api_key.revoked",
            target_type="api_key",
            target_id=api_key.id,
            actor_user_id=ctx.user.id,
            project_id=project.id,
            details={"name": api_key.name, "prefix": api_key.prefix},
        )
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@key_auth_router.get("/self", response_model=ApiKeyIdentity, summary="Identify the calling project API key")
def api_key_identity(ctx: CurrentApiKey) -> ApiKeyIdentity:
    """Validates the bearer API key and returns the project it is scoped to."""
    return ApiKeyIdentity(
        project_id=ctx.project.id, project_slug=ctx.project.slug, key_id=ctx.api_key.id, key_prefix=ctx.api_key.prefix
    )
