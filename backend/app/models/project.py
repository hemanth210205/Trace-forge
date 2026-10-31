import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, utcnow


class Project(CreatedAtMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "slug"),
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]*$'", name="slug_format"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    agent_name: Mapped[str] = mapped_column(String(100))
    agent_version: Mapped[str | None] = mapped_column(String(50))
    environment: Mapped[str] = mapped_column(String(32), default="development", server_default="development")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    is_demo: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=text("now()"))


class ProjectApiKey(CreatedAtMixin, Base):
    """Project-scoped ingestion key. Stores an HMAC-SHA256 of the full key, never the key itself."""

    __tablename__ = "project_api_keys"
    __table_args__ = (Index("ix_project_api_keys_project_id", "project_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(8), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
