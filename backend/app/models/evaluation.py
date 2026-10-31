"""Datasets, immutable dataset versions, evaluation runs and per-case results.

Immutability and the run state machine are enforced in PostgreSQL (triggers in migration 0003), not only
in the API: published version rows cannot be updated, a run can only move along
pending -> running -> scoring -> completed | incomplete | failed (or be aborted to failed), and results
cannot be written once their run is terminal.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, utcnow

RUN_STATUSES = ("pending", "running", "scoring", "completed", "incomplete", "failed")
TERMINAL_STATUSES = ("completed", "incomplete", "failed")


class Dataset(CreatedAtMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("project_id", "name"),)  # its index also serves project_id lookups

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=text("now()"))


class DatasetCase(CreatedAtMixin, Base):
    """A case in the dataset's editable draft. Publishing copies the draft into a new immutable version."""

    __tablename__ = "dataset_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "case_key"),
        Index("ix_dataset_cases_dataset_id_position", "dataset_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    case_key: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    input: Mapped[Any] = mapped_column(JSONB)
    expected_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    expected_sources: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=text("now()"))


class DatasetVersion(CreatedAtMixin, Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("case_count >= 1", name="not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    case_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class DatasetVersionCase(Base):
    __tablename__ = "dataset_version_cases"
    __table_args__ = (
        UniqueConstraint("version_id", "case_key"),
        Index("ix_dataset_version_cases_version_id_position", "version_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    case_key: Mapped[str] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
    input: Mapped[Any] = mapped_column(JSONB)
    expected_output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    expected_sources: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))


class EvaluationRun(CreatedAtMixin, Base):
    __tablename__ = "evaluation_runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in RUN_STATUSES)})", name="status_valid"),
        CheckConstraint(
            f"(status IN ({', '.join(repr(s) for s in TERMINAL_STATUSES)})) = (finished_at IS NOT NULL)",
            name="finished_iff_terminal",
        ),
        Index("ix_evaluation_runs_project_id_created_at", "project_id", text("created_at DESC")),
        Index("ix_evaluation_runs_dataset_version_id", "dataset_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    evaluators: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    total_cases: Mapped[int] = mapped_column(Integer)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None]
    scoring_started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Counts and per-evaluator statistics, written once when the run reaches a terminal state.
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "case_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_version_cases.id", ondelete="CASCADE"))
    output: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    execution_error: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Trace reported by the executor; only shown as a link once scoring verified it exists in the project.
    trace_id: Mapped[uuid.UUID | None]
    trace_found: Mapped[bool | None]
    latency_ms: Mapped[float | None]
    submitted_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))
    passed: Mapped[bool | None]
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    evaluations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    scored_at: Mapped[datetime | None]
