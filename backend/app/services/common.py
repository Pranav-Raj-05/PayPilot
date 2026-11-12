import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from sqlalchemy import select

from app.models import Automation, AuditLog, Notification

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


def payment_notifications_enabled(db: Session, user_id) -> bool:
    """The "Payment notifications" automation switch. No row yet (a user who never opened Automations) means the default: on."""
    a = db.scalars(select(Automation).where(Automation.user_id == as_uuid(user_id), Automation.automation_type == "payment_notifications")).first()
    return True if a is None else bool(a.is_enabled)


def notify_payment(db: Session, user_id, title: str, message: str, kind: str = "info", data: dict | None = None) -> Notification | None:
    """In-app notification about a payment outcome (received / failed / refunded). Honors the user's automation switch.
    Alerts about discrepancies (amount mismatch, unmatched payment, refund anomalies) use notify() directly and are never suppressed."""
    if not payment_notifications_enabled(db, user_id):
        return None
    return notify(db, user_id, title, message, kind, data)
