"""Invitations and the registration policy (``TRACEFORGE_REGISTRATION``).

Registration modes apply to every way an account is created: the password form *and* Google/GitHub sign-in.
Signing in to an existing account is never affected.

Invitations (mode ``invite``):
- Tied to one email address; created only for an email that has no account yet.
- The code (``secrets.token_urlsafe``) is shown once by ``python -m app.accounts invite create``; only its SHA-256
  is stored, and no code is ever logged.
- Consumed with a single conditional ``UPDATE ... WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > now``
  inside the transaction that creates the account. Two concurrent requests for the same invitation serialise on the
  row lock; the second sees ``used_at`` set and matches nothing. If account creation fails, the transaction rolls
  back and the invitation stays pending.
- Password registration must present the code (it proves possession of the invitation; Traceforge does not verify
  email addresses itself). Google/GitHub sign-in may use any pending invitation for the provider-*verified* email.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.models import Invitation, User
from app.schemas.auth import canonical_email


class RegistrationRefused(Exception):
    """A new account may not be created. ``code`` is the stable error code shown to the client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvitationError(Exception):
    pass


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _pending(now: datetime):  # type: ignore[no-untyped-def]
    return (Invitation.used_at.is_(None), Invitation.revoked_at.is_(None), Invitation.expires_at > now)


# --- policy --------------------------------------------------------------------------------------------------


def check_before_create(settings: Settings, email: str, *, has_code: bool, verified_email: bool) -> None:
    """Refusals that do not depend on the database; called before the account row exists."""
    mode = settings.registration
    if mode == "disabled":
        raise RegistrationRefused("registration_closed", "Registration is closed on this server.")
    if mode == "domain":
        domain = email.rsplit("@", 1)[-1]
        if domain not in settings.registration_domain_list:
            raise RegistrationRefused("domain_not_allowed", "Accounts with this email domain cannot be created here.")
    if mode == "invite" and not has_code and not verified_email:
        raise RegistrationRefused("invitation_required", "An invitation is required to create an account.")


def consume_for_new_account(
    db: Session, settings: Settings, user: User, *, code: str | None, verified_email: bool, now: datetime | None = None
) -> uuid.UUID | None:
    """In ``invite`` mode, uses one invitation for ``user`` (flushed, not committed) or raises. Other modes: no-op."""
    if settings.registration != "invite":
        return None
    now = now or utcnow()
    if code:
        target = select(Invitation.id).where(Invitation.token_hash == hash_code(code), Invitation.email == user.email, *_pending(now))
    elif verified_email:
        # Oldest pending invitation for this verified address; SKIP LOCKED lets a concurrent sign-in take another.
        target = (
            select(Invitation.id)
            .where(Invitation.email == user.email, *_pending(now))
            .order_by(Invitation.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    else:
        raise RegistrationRefused("invitation_required", "An invitation is required to create an account.")
    used = db.execute(
        update(Invitation)
        .where(Invitation.id == target.scalar_subquery(), *_pending(now))
        .values(used_at=now, used_by_user_id=user.id)
        .returning(Invitation.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if used is None:
        raise RegistrationRefused("invitation_invalid", "This invitation is invalid, expired, revoked or already used.")
    return used


# --- management (python -m app.accounts) ---------------------------------------------------------------------


@dataclass(frozen=True)
class CreatedInvitation:
    invitation: Invitation
    code: str


def status(invitation: Invitation, now: datetime | None = None) -> str:
    now = now or utcnow()
    if invitation.used_at is not None:
        return "used"
    if invitation.revoked_at is not None:
        return "revoked"
    if invitation.expires_at <= now:
        return "expired"
    return "pending"


def create(db: Session, email: str, *, ttl_days: int, note: str = "", now: datetime | None = None) -> CreatedInvitation:
    from email_validator import EmailNotValidError, validate_email

    try:
        validate_email(email.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvitationError(f"Invalid email address: {exc}") from None
    email = canonical_email(email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        # An invitation must never become a way into an existing account.
        raise InvitationError("An account with this email already exists; it can sign in normally.")
    if not 1 <= ttl_days <= 90:
        raise InvitationError("Expiry must be between 1 and 90 days.")
    now = now or utcnow()
    code = secrets.token_urlsafe(32)
    invitation = Invitation(email=email, token_hash=hash_code(code), note=note[:200], expires_at=now + timedelta(days=ttl_days))
    db.add(invitation)
    db.flush()
    return CreatedInvitation(invitation, code)


def listing(db: Session, *, include_inactive: bool = False, now: datetime | None = None) -> list[Invitation]:
    query = select(Invitation).order_by(Invitation.created_at.desc())
    if not include_inactive:
        query = query.where(*_pending(now or utcnow()))
    return list(db.scalars(query))


def revoke(db: Session, target: str, now: datetime | None = None) -> int:
    """Revokes pending invitations by id or by email; returns how many were revoked."""
    now = now or utcnow()
    try:
        condition = Invitation.id == uuid.UUID(target)
    except ValueError:
        condition = Invitation.email == canonical_email(target)
    result = db.execute(
        update(Invitation).where(condition, *_pending(now)).values(revoked_at=now).execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


def prune(db: Session, *, older_than_days: int = 30, now: datetime | None = None) -> int:
    """Deletes used, revoked or expired invitations older than the given age; pending ones are kept."""
    now = now or utcnow()
    cutoff = now - timedelta(days=older_than_days)
    result = db.execute(
        delete(Invitation)
        .where(Invitation.created_at < cutoff)
        .where(or_(Invitation.used_at.is_not(None), Invitation.revoked_at.is_not(None), Invitation.expires_at <= now))
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
