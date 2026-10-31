import base64
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import cast, or_, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB

from app.api.deps import CurrentAuth, DbSession
from app.api.routes.projects import get_owned_project
from app.core.errors import AppError, NotFound
from app.models import Span, Trace
from app.schemas.trace import SpanOut, TraceDetail, TracePage, TraceSummary

router = APIRouter(prefix="/projects/{project_id}/traces", tags=["traces"])

_NOT_FOUND = {404: {"description": "Project or trace does not exist or is not owned by the caller"}}


def _duration_ms(started: datetime, ended: datetime | None) -> float | None:
    return None if ended is None else round((ended - started).total_seconds() * 1000, 3)


def _summary(t: Trace) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "status": t.status,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
        "duration_ms": _duration_ms(t.started_at, t.ended_at),
        "span_count": t.span_count,
        "error_count": t.error_count,
        "total_tokens": t.total_tokens,
        "total_cost_usd": t.total_cost_usd,
        "tags": t.tags,
        "user_id": t.user_ref,
        "session_id": t.session_ref,
        "has_trace_record": t.has_trace_record,
    }


def _encode_cursor(t: Trace) -> str:
    return base64.urlsafe_b64encode(f"{t.started_at.isoformat()}|{t.id}".encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        started, trace_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        return datetime.fromisoformat(started), uuid.UUID(trace_id)
    except (ValueError, UnicodeDecodeError):
        raise AppError(422, "invalid_cursor", "The pagination cursor is invalid.") from None


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("", response_model=TracePage, responses=_NOT_FOUND)
def list_traces(
    project_id: uuid.UUID,
    ctx: CurrentAuth,
    db: DbSession,
    status: Literal["ok", "error", "unset"] | None = None,
    q: Annotated[str | None, Query(max_length=200, description="Name substring or exact trace id")] = None,
    tag: Annotated[str | None, Query(max_length=64)] = None,
    has_errors: bool | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> TracePage:
    """Newest first, keyset-paginated: pass ``next_cursor`` back as ``cursor`` for the next page."""
    project = get_owned_project(db, ctx.user.id, project_id)
    stmt = select(Trace).where(Trace.project_id == project.id)
    if status:
        stmt = stmt.where(Trace.status == status)
    if q:
        conditions = [Trace.name.ilike(f"%{_like_escape(q)}%", escape="\\")]
        try:
            conditions.append(Trace.id == uuid.UUID(q.strip()))
        except ValueError:
            pass
        stmt = stmt.where(or_(*conditions))
    if tag:
        stmt = stmt.where(Trace.tags.contains(cast([tag], JSONB)))
    if has_errors is not None:
        stmt = stmt.where(Trace.error_count > 0 if has_errors else Trace.error_count == 0)
    if since:
        stmt = stmt.where(Trace.started_at >= since)
    if until:
        stmt = stmt.where(Trace.started_at < until)
    if cursor:
        started, trace_id = _decode_cursor(cursor)
        stmt = stmt.where(tuple_(Trace.started_at, Trace.id) < tuple_(started, trace_id))
    rows = list(db.scalars(stmt.order_by(Trace.started_at.desc(), Trace.id.desc()).limit(limit + 1)))
    page, more = rows[:limit], len(rows) > limit
    return TracePage(
        items=[TraceSummary(**_summary(t)) for t in page], next_cursor=_encode_cursor(page[-1]) if more else None
    )


@router.get("/{trace_id}", response_model=TraceDetail, responses=_NOT_FOUND)
def get_trace(project_id: uuid.UUID, trace_id: uuid.UUID, ctx: CurrentAuth, db: DbSession) -> TraceDetail:
    """The trace with all of its spans, ordered by start time (the client builds the tree)."""
    project = get_owned_project(db, ctx.user.id, project_id)
    trace = db.get(Trace, (project.id, trace_id))
    if trace is None:
        raise NotFound("trace")
    spans = db.scalars(
        select(Span)
        .where(Span.project_id == project.id, Span.trace_id == trace.id)
        .order_by(Span.started_at, Span.id)
    )
    return TraceDetail(
        **_summary(trace),
        input=trace.input,
        output=trace.output,
        metadata=trace.metadata_,
        spans=[
            SpanOut(
                id=s.id,
                trace_id=s.trace_id,
                parent_span_id=s.parent_span_id,
                name=s.name,
                kind=s.kind,
                status=s.status,
                started_at=s.started_at,
                ended_at=s.ended_at,
                duration_ms=_duration_ms(s.started_at, s.ended_at),
                input=s.input,
                output=s.output,
                metadata=s.metadata_,
                attributes=s.attributes,
                error=s.error,
                model=s.model,
                prompt_tokens=s.prompt_tokens,
                completion_tokens=s.completion_tokens,
                total_tokens=s.total_tokens,
                cost_usd=s.cost_usd,
            )
            for s in spans
        ],
    )
