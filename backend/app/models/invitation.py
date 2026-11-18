import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin


class Invitation(CreatedAtMixin, Base):
    """An invitation to create one account for one email address (``TRACEFORGE_REGISTRATION=invite``).

    Only a SHA-256 hash of the invitation code is stored. An invitation is *pending* while it is unused, unrevoked
    and unexpired; using it sets ``used_at`` in the same transaction that creates the account (see
    ``app/services/invitations.py``), so it can never be used twice.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_invitations_email", "email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    note: Mapped[str] = mapped_column(String(200), default="", server_default=text("''"))
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    used_at: Mapped[datetime | None]
    used_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
