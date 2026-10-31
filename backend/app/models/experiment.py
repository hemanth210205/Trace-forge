"""Controlled experiments: one baseline and one candidate configuration replayed on the same dataset version.

Each arm is an ordinary evaluation run (same dataset version and evaluators as the source run), so scoring,
the run state machine and result immutability are inherited from M5. Migration 0005 adds database rules:
both arm runs must belong to the experiment's project and dataset version; configurations never change; the
experiment follows pending -> running -> completed | failed and is frozen once finished; promotion decisions
are append-only and only allowed on completed experiments.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin

EXPERIMENT_STATUSES = ("pending", "running", "completed", "failed")


class Experiment(CreatedAtMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(f"status IN ({', '.join(repr(s) for s in EXPERIMENT_STATUSES)})", name="status_valid"),
        CheckConstraint("(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)", name="finished_iff_terminal"),
        CheckConstraint("baseline_config <> candidate_config", name="configs_differ"),
        CheckConstraint("baseline_run_id <> candidate_run_id", name="distinct_arms"),
        Index("ix_experiments_project_id_created_at", "project_id", text("created_at DESC")),
        Index("ix_experiments_source_run_id", "source_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    source_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"))
    dataset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    baseline_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), unique=True)
    candidate_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_runs.id", ondelete="CASCADE"), unique=True)
    # The diagnosis hypothesis this experiment tests, if any. Its recommendation is context, not evidence.
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("diagnosis_hypotheses.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    baseline_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    candidate_config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    config_diff: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None]
    deadline_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    failure_reason: Mapped[str | None] = mapped_column(Text)
    # Written once on completion: aggregates, deltas, fixes/regressions, measurement availability.
    comparison: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class ExperimentDecision(Base):
    """Append-only human promotion decisions. Approving records intent; it changes no agent or deployment."""

    __tablename__ = "experiment_decisions"
    __table_args__ = (
        CheckConstraint("decision IN ('approve', 'reject')", name="decision_valid"),
        CheckConstraint("length(btrim(rationale)) >= 3", name="rationale_required"),
        Index("ix_experiment_decisions_experiment_id", "experiment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
