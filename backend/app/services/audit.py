import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

# Detail keys that must never be written to the audit log, whatever the caller passes.
_FORBIDDEN_DETAIL_KEYS = frozenset({"password", "password_hash", "token", "token_hash", "key", "key_hash", "secret"})


def record_audit(
    db: Session,
    *,
    action: str,
    target_type: str,
    target_id: uuid.UUID | str | None,
    actor_user_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Adds an audit record to the current transaction. ``details`` must hold identifiers only, never credentials."""
    details = details or {}
    leaked = _FORBIDDEN_DETAIL_KEYS.intersection(details)
    if leaked:
        raise ValueError(f"audit details must not contain credential fields: {sorted(leaked)}")
    entry = AuditLog(
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        actor_user_id=actor_user_id,
        project_id=project_id,
        details=details,
    )
    db.add(entry)
    return entry
