"""Telemetry ingested from the SDK.

Traces and spans are keyed by ``(project_id, id)``: ids are generated client-side (so retries are
idempotent upserts) and are only meaningful inside their project, so one project can never address or
overwrite another project's telemetry. ``parent_span_id`` deliberately has no foreign key: spans are sent
when they end, so a child routinely arrives before its parent.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, utcnow

SPAN_KINDS = ("llm", "tool", "retrieval", "agent", "chain", "custom")
STATUSES = ("unset", "ok", "error")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Trace(CreatedAtMixin, Base):
    __tablename__ = "traces"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        CheckConstraint(
            "span_count >= 0 AND error_count >= 0 AND total_tokens >= 0 AND total_cost_usd >= 0",
            name="aggregates_non_negative",
        ),
        Index("ix_traces_project_id_started_at", "project_id", text("started_at DESC"), text("id DESC")),
        Index("ix_traces_project_id_status", "project_id", "status"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), server_default="unset")
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    input: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    user_ref: Mapped[str | None] = mapped_column(String(200))
    session_ref: Mapped[str | None] = mapped_column(String(200))
    # Whether the SDK has sent the trace record itself, or only spans referencing it so far.
    has_trace_record: Mapped[bool] = mapped_column(server_default=text("false"))
    # Maintained by ingestion from the trace's spans.
    span_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, server_default="0")
    total_tokens: Mapped[int] = mapped_column(Integer, server_default="0")
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=text("now()"))


class Span(CreatedAtMixin, Base):
    __tablename__ = "spans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "trace_id"], ["traces.project_id", "traces.id"], ondelete="CASCADE"
        ),
        CheckConstraint(_in("kind", SPAN_KINDS), name="kind_valid"),
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ended_after_started"),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) AND (completion_tokens IS NULL OR completion_tokens >= 0)"
            " AND (total_tokens IS NULL OR total_tokens >= 0) AND (cost_usd IS NULL OR cost_usd >= 0)",
            name="usage_non_negative",
        ),
        Index("ix_spans_project_id_trace_id_started_at", "project_id", "trace_id", "started_at"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    trace_id: Mapped[uuid.UUID]
    parent_span_id: Mapped[uuid.UUID | None]
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="unset")
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    input: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    # Kind-specific details, e.g. tool name/arguments or retrieved documents.
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int | None]
    completion_tokens: Mapped[int | None]
    total_tokens: Mapped[int | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
