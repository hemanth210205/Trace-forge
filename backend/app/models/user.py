import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin


class User(CreatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="email_lowercase"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class UserSession(CreatedAtMixin, Base):
    """Server-side console session. Only a SHA-256 hash of the cookie token is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(lazy="joined")
