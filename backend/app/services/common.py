import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import AuditLog, Notification

log = logging.getLogger("paypilot.audit")


def as_uuid(value: Any) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise AppError("Invalid identifier", code="invalid_id", status_code=422) from None


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def audit(db: Session, user_id, action: str, entity_type: str | None = None, entity_id: Any = None, detail: dict | None = None) -> None:
    db.add(AuditLog(user_id=as_uuid(user_id) if user_id else None, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id else None, detail=detail))
    log.info("audit", extra={"event": action, "user_id": str(user_id) if user_id else None})


def notify(db: Session, user_id, title: str, message: str, kind: str = "info", data: dict | None = None) -> Notification:
    n = Notification(user_id=as_uuid(user_id), title=title, message=message, notification_type=kind, data=data)
    db.add(n)
    return n
