"""Idempotent, project-scoped persistence of SDK telemetry batches.

Every row is written with the project of the authenticating API key, so a batch can only ever create or
update telemetry inside that project. Re-sending a batch (SDK retries) upserts the same rows.
"""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Span, Trace
from app.schemas.trace import IngestBatch, SpanIn, TraceIn


def _last_wins(items: list[Any]) -> list[Any]:
    """Drops duplicate ids within one batch (PostgreSQL rejects one upsert touching a row twice)."""
    by_id = {item.id: item for item in items}
    return list(by_id.values())


def _trace_row(project_id: uuid.UUID, t: TraceIn) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "id": t.id,
        "name": t.name,
        "status": t.status,
        "started_at": t.started_at,
        "ended_at": t.ended_at,
        "input": t.input,
        "output": t.output,
        "metadata": t.metadata,
        "tags": t.tags,
        "user_ref": t.user_id,
        "session_ref": t.session_id,
        "has_trace_record": True,
    }


def _span_row(project_id: uuid.UUID, s: SpanIn) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "id": s.id,
        "trace_id": s.trace_id,
        "parent_span_id": s.parent_span_id,
        "name": s.name,
        "kind": s.kind,
        "status": s.status,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "input": s.input,
        "output": s.output,
        "metadata": s.metadata,
        "attributes": s.attributes,
        "error": s.error.model_dump() if s.error else None,
        "model": s.model,
        "prompt_tokens": s.prompt_tokens,
        "completion_tokens": s.completion_tokens,
        "total_tokens": s.total_tokens
        if s.total_tokens is not None
        else (
            (s.prompt_tokens or 0) + (s.completion_tokens or 0)
            if s.prompt_tokens is not None or s.completion_tokens is not None
            else None
        ),
        "cost_usd": s.cost_usd,
    }


def ingest_batch(db: Session, project_id: uuid.UUID, batch: IngestBatch) -> tuple[int, int]:
    """Upserts the batch in the caller's transaction and refreshes the affected traces' aggregates."""
    traces = _last_wins(batch.traces)
    spans = _last_wins(batch.spans)
    table_t, table_s = Trace.__table__, Span.__table__

    if traces:
        stmt = insert(table_t).values([_trace_row(project_id, t) for t in traces])
        updatable = [c for c in _trace_row(project_id, traces[0]) if c not in ("project_id", "id")]
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["project_id", "id"],
                set_={**{c: stmt.excluded[c] for c in updatable}, "updated_at": func.now()},
            )
        )

    if spans:
        # Spans may arrive before their trace record (or without one, if the agent crashed): create a
        # placeholder trace so the foreign key holds; the real trace record overwrites it when it arrives.
        known = {t.id for t in traces}
        placeholders: dict[uuid.UUID, dict[str, Any]] = {}
        for s in spans:
            if s.trace_id in known:
                continue
            row = placeholders.get(s.trace_id)
            if row is None or s.started_at < row["started_at"]:
                placeholders[s.trace_id] = {
                    "project_id": project_id,
                    "id": s.trace_id,
                    "name": s.name if s.parent_span_id is None else "(trace record not received)",
                    "started_at": s.started_at,
                }
        if placeholders:
            db.execute(insert(table_t).values(list(placeholders.values())).on_conflict_do_nothing())

        stmt = insert(table_s).values([_span_row(project_id, s) for s in spans])
        updatable = [c for c in _span_row(project_id, spans[0]) if c not in ("project_id", "id")]
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["project_id", "id"], set_={c: stmt.excluded[c] for c in updatable}
            )
        )

    affected = {t.id for t in traces} | {s.trace_id for s in spans}
    _refresh_aggregates(db, project_id, affected)
    return len(traces), len(spans)


def _refresh_aggregates(db: Session, project_id: uuid.UUID, trace_ids: set[uuid.UUID]) -> None:
    if not trace_ids:
        return
    stats = (
        select(
            Span.trace_id.label("trace_id"),
            func.count().label("span_count"),
            func.count().filter(Span.status == "error").label("error_count"),
            func.coalesce(func.sum(Span.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(Span.cost_usd), 0).label("total_cost_usd"),
            func.min(Span.started_at).label("first_started_at"),
        )
        .where(Span.project_id == project_id, Span.trace_id.in_(trace_ids))
        .group_by(Span.trace_id)
        .subquery()
    )
    db.execute(
        update(Trace)
        .where(Trace.project_id == project_id, Trace.id == stats.c.trace_id)
        .values(
            span_count=stats.c.span_count,
            error_count=stats.c.error_count,
            total_tokens=stats.c.total_tokens,
            total_cost_usd=stats.c.total_cost_usd,
            # Placeholders start at their earliest span; real trace records keep their own start.
            started_at=func.least(Trace.started_at, stats.c.first_started_at),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
