"""Persisted failure diagnoses of evaluation runs.

A diagnosis is a historical record: re-running creates a new row (``retry_of`` points at the previous one) and
a completed or failed diagnosis is immutable (migration 0004 triggers). Hypotheses carry two separate
judgements: ``evidence_status`` (what the persisted trace/evaluation evidence says, computed server-side) and
``validation_status`` (``validated`` only with an experiment reference, which M6 never sets).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin

DIAGNOSIS_STATUSES = ("pending", "running", "completed", "failed")
CATEGORIES = (
    "retrieval_failure",
    "context_selection_failure",
    "grounding_failure",
    "tool_execution_failure",
    "tool_selection_failure",
    "reasoning_failure",
    "output_format_failure",
    "insufficient_evidence",
)
EVIDENCE_STATUSES = ("supported", "unsupported", "contradicted", "inconclusive")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Diagnosis(CreatedAtMixin, Base):
    __tablename__ = "diagnoses"
    __table_args__ = (
        CheckConstraint(_in("status", DIAGNOSIS_STATUSES), name="status_valid"),
        CheckConstraint("(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)", name="finished_iff_terminal"),
        Index("ix_diagnoses_project_id_created_at", "project_id", text("created_at DESC")),
        Index("ix_diagnoses_run_id", "run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    retry_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("diagnoses.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class DiagnosisCase(Base):
    __tablename__ = "diagnosis_cases"
    __table_args__ = (UniqueConstraint("diagnosis_id", "result_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diagnoses.id", ondelete="CASCADE"))
    result_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_results.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    case_key: Mapped[str] = mapped_column(String(100))
    # Only set when the trace exists in the project (verified while gathering evidence).
    trace_id: Mapped[uuid.UUID | None]
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    missing_evidence: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")


class DiagnosisHypothesis(Base):
    __tablename__ = "diagnosis_hypotheses"
    __table_args__ = (
        CheckConstraint(_in("category", CATEGORIES), name="category_valid"),
        CheckConstraint(_in("evidence_status", EVIDENCE_STATUSES), name="evidence_status_valid"),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        CheckConstraint("validation_status IN ('unvalidated', 'validated')", name="validation_status_valid"),
        CheckConstraint("(validation_status = 'validated') = (validated_by IS NOT NULL)", name="validated_needs_reference"),
        Index("ix_diagnosis_hypotheses_diagnosis_id", "diagnosis_id"),
        Index("ix_diagnosis_hypotheses_case_id", "case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    diagnosis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diagnoses.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diagnosis_cases.id", ondelete="CASCADE"))
    rank: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40))
    explanation: Mapped[str] = mapped_column(Text)
    supporting_facts: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    contradicting_facts: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    evidence_trace_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    evidence_span_ids: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    evidence_status: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    uncertainty: Mapped[str] = mapped_column(Text, default="", server_default="")
    missing_evidence: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    recommended_experiment: Mapped[str] = mapped_column(Text, default="", server_default="")
    validation_status: Mapped[str] = mapped_column(String(16), default="unvalidated", server_default="unvalidated")
    validated_by: Mapped[str | None] = mapped_column(Text)
