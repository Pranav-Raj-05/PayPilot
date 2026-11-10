"""Automations that are ACTUALLY implemented. Each runs from the background scheduler (or 'run now') and writes
notifications. Nothing here sends external email/SMS - reminders are drafted for user approval."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Automation, Invoice, Notification, Transaction
from app.services.analytics import AnalyticsService
from app.services.common import aware, as_uuid, notify
from app.services.invoices import InvoiceService

log = logging.getLogger("paypilot.automations")

CATALOG = {
    "daily_revenue_summary": ("Daily revenue summary", "Posts yesterday's revenue and payment count as a notification once per day.", {}),
    "failed_payment_monitor": ("Failed payment monitoring", "Alerts you when new payments fail.", {}),
    "overdue_reminder_suggestions": ("Payment reminder suggestions", "Finds overdue invoices and drafts reminders for your approval. Nothing is sent automatically.", {"grace_days": 3}),
    "payment_notifications": ("Payment notifications", "In-app notifications when payments are completed, fail or are refunded (triggered by verified provider events).", {}),
}


def ensure_defaults(db: Session, user_id) -> list[Automation]:
    uid = as_uuid(user_id)
    existing = {a.automation_type: a for a in db.scalars(select(Automation).where(Automation.user_id == uid)).all()}
    for kind, (name, desc, cfg) in CATALOG.items():
        if kind not in existing:
            a = Automation(user_id=uid, name=name, description=desc, automation_type=kind, is_enabled=(kind == "payment_notifications"), configuration=cfg)
            db.add(a)
            existing[kind] = a
    db.flush()
    return sorted(existing.values(), key=lambda a: list(CATALOG).index(a.automation_type))


def run_automation(db: Session, a: Automation, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    uid = a.user_id
    msg = "nothing to do"
    if a.automation_type == "daily_revenue_summary":
        start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        rows = [t for t in AnalyticsService(db, uid, now)._txns(start, end) if t.status == "completed"]
        total = sum(float(t.amount) for t in rows)
        cur = rows[0].currency if rows else "USD"
        notify(db, uid, "Daily revenue summary", f"{len(rows)} payment(s), {cur} {total:,.2f} received on {start.date()}.", "digest")
        msg = f"{len(rows)} payments summarised"
    elif a.automation_type == "failed_payment_monitor":
        since = aware(a.last_executed_at) or (now - timedelta(days=1))
        failed = [t for t in db.scalars(select(Transaction).where(Transaction.user_id == uid, Transaction.status == "failed", Transaction.updated_at >= since)).unique().all()]
        if failed:
            notify(db, uid, "Failed payments detected", f"{len(failed)} payment(s) failed since the last check.", "alert", {"transaction_ids": [str(t.id) for t in failed]})
        msg = f"{len(failed)} failed payments"
    elif a.automation_type == "overdue_reminder_suggestions":
        InvoiceService(db, uid).mark_overdue()
        grace = int((a.configuration or {}).get("grace_days", 3))
        cutoff = now.date() - timedelta(days=grace)
        overdue = list(db.scalars(select(Invoice).where(Invoice.user_id == uid, Invoice.status == "overdue", Invoice.due_date <= cutoff)).unique().all())
        for inv in overdue:
            already = db.scalar(select(Notification.id).where(Notification.user_id == uid, Notification.notification_type == "reminder_suggestion",
                                                              Notification.created_at >= now - timedelta(days=2),
                                                              Notification.message.contains(inv.invoice_number)))
            if already:
                continue
            who = inv.customer.name if inv.customer else "the customer"
            notify(db, uid, "Reminder suggested", f"{inv.invoice_number} for {who} is overdue ({inv.currency} {inv.total_amount}). Review and send a reminder.",
                   "reminder_suggestion", {"invoice_id": str(inv.id), "requires_approval": True})
        msg = f"{len(overdue)} overdue invoices reviewed"
    else:
        return "event-driven (no scheduled run)"
    a.last_executed_at = now
    return msg


def run_due(db: Session, now: datetime | None = None, min_interval_hours: int = 20) -> int:
    """Scheduler tick: run every enabled scheduled automation that has not run recently."""
    now = now or datetime.now(timezone.utc)
    ran = 0
    for a in db.scalars(select(Automation).where(Automation.is_enabled.is_(True))).all():
        if a.automation_type == "payment_notifications":
            continue
        last = aware(a.last_executed_at)
        if last and now - last < timedelta(hours=min_interval_hours if a.automation_type == "daily_revenue_summary" else 1):
            continue
        try:
            run_automation(db, a, now)
            ran += 1
        except Exception:  # one user's failure must not stop the others
            log.exception("automation %s failed", a.id)
    return ran
