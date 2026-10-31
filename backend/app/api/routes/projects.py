import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import CsrfAuth, CurrentAuth, DbSession
from app.core.errors import AppError, NotFound
from app.db.errors import constraint_name
from app.models import Project
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate, slugify
from app.services.audit import record_audit

router = APIRouter(prefix="/projects", tags=["projects"])

_NOT_FOUND = {404: {"description": "Project does not exist or is not owned by the caller"}}


def get_owned_project(db: Session, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project:
    """The single ownership check for project-scoped routes. Other users' projects are indistinguishable from missing ones."""
    project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))
    if project is None:
        raise NotFound("project")
    return project


def _flush_or_slug_conflict(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if constraint_name(exc) == "uq_projects_owner_id_slug":
            raise AppError(409, "slug_taken", "You already have a project with this slug.") from None
        raise


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut, responses={409: {"description": "Slug taken"}})
def create_project(body: ProjectCreate, ctx: CsrfAuth, db: DbSession) -> Project:
    data = body.model_dump()
    data["slug"] = data["slug"] or slugify(body.name)
    project = Project(owner_id=ctx.user.id, **data)
    db.add(project)
    _flush_or_slug_conflict(db)
    db.commit()
    return project


@router.get("", response_model=list[ProjectOut])
def list_projects(ctx: CurrentAuth, db: DbSession) -> list[Project]:
    return list(db.scalars(select(Project).where(Project.owner_id == ctx.user.id).order_by(Project.created_at.desc())))


@router.get("/{project_id}", response_model=ProjectOut, responses=_NOT_FOUND)
def get_project(project_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> Project:
    return get_owned_project(db, ctx.user.id, project_id)


@router.patch("/{project_id}", response_model=ProjectOut, responses={**_NOT_FOUND, 409: {"description": "Slug taken"}})
def update_project(project_id: uuid.UUID, body: ProjectUpdate, ctx: CsrfAuth, db: DbSession) -> Project:
    project = get_owned_project(db, ctx.user.id, project_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    _flush_or_slug_conflict(db)
    db.commit()
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def delete_project(project_id: uuid.UUID, ctx: CsrfAuth, db: DbSession) -> Response:
    project = get_owned_project(db, ctx.user.id, project_id)
    # Written in the same transaction; audit_log.project_id is SET NULL by the delete, target_id keeps the id.
    record_audit(
        db,
        action="project.deleted",
        target_type="project",
        target_id=project.id,
        actor_user_id=ctx.user.id,
        project_id=project.id,
        details={"name": project.name, "slug": project.slug},
    )
    db.flush()
    db.delete(project)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
