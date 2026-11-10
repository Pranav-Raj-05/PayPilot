"""profile/auth-adjacent, dashboard, analytics, notifications, automations, settings."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import get_paypal, get_razorpay
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import NotFound
from app.core.ratelimit import rate_limit
from app.core.security import CurrentUser, get_current_user
from app.models import Automation, Notification, Profile, WebhookEvent
from app.payments.paypal.client import PayPalClient
from app.payments.razorpay.client import RazorpayClient
from app.schemas.entities import AutomationOut, AutomationUpdate, NotificationOut, ProfileOut, ProfileUpdate, TransactionOut
from app.services import automations as auto
from app.services.analytics import AnalyticsService
from app.services.common import as_uuid, audit

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
analytics_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["notifications"])
automations_router = APIRouter(prefix="/api/automations", tags=["automations"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])

Days = Annotated[int, Query(ge=1, le=365)]


def paypal_status(settings: Settings, paypal: PayPalClient, db: Session, probe: bool = True) -> dict:
    out = {"configured": settings.paypal_configured, "mode": settings.paypal_mode, "webhook_configured": bool(settings.paypal_webhook_id),
           "client_id_hint": f"••••{settings.paypal_client_id[-4:]}" if settings.paypal_client_id else None, "connected": False, "latency_ms": None, "error": None}
    last = db.scalars(select(WebhookEvent).where(WebhookEvent.provider == "paypal").order_by(WebhookEvent.created_at.desc())).first()
    out["last_webhook_at"] = last.created_at.isoformat() if last else None
    if settings.paypal_configured and probe:
        try:
            out["latency_ms"] = round(paypal.ping())
            out["connected"] = True
        except Exception as exc:  # status endpoint must never 500
            out["error"] = getattr(exc, "message", "Could not authenticate with PayPal")
    return out


# ------------------------------------------------------------------ auth / profile
@auth_router.get("/me", response_model=ProfileOut)
def me(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.get(Profile, as_uuid(user.id))


# ------------------------------------------------------------------ dashboard
@dashboard_router.get("")
def dashboard(days: Days = 30, currency: str | None = None, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings), paypal: PayPalClient = Depends(get_paypal)):
    data = AnalyticsService(db, user.id).overview(days, currency)
    data["recent_transactions"] = [TransactionOut.model_validate(t).model_dump(mode="json") for t in data["recent_transactions"]]
    data["paypal"] = paypal_status(settings, paypal, db, probe=False)
    return data


# ------------------------------------------------------------------ analytics
@analytics_router.get("")
def analytics(days: Days = 30, currency: str | None = None, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return AnalyticsService(db, user.id).analytics(days, currency)


# ------------------------------------------------------------------ notifications
@notifications_router.get("")
def list_notifications(unread_only: bool = False, limit: int = Query(30, ge=1, le=100), user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == as_uuid(user.id))
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit)).all()
    unread = db.scalars(select(Notification.id).where(Notification.user_id == as_uuid(user.id), Notification.is_read.is_(False))).all()
    return {"unread": len(unread), "items": [NotificationOut.model_validate(n).model_dump(mode="json") for n in rows]}


@notifications_router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    n = db.scalars(select(Notification).where(Notification.id == as_uuid(notification_id), Notification.user_id == as_uuid(user.id))).first()
    if not n:
        raise NotFound("Notification not found")
    n.is_read = True
    return n


@notifications_router.post("/read-all")
def mark_all_read(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == as_uuid(user.id)).values(is_read=True))
    return {"ok": True}


# ------------------------------------------------------------------ automations
@automations_router.get("", response_model=list[AutomationOut])
def list_automations(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return auto.ensure_defaults(db, user.id)


def _own(db, user, automation_id) -> Automation:
    a = db.scalars(select(Automation).where(Automation.id == as_uuid(automation_id), Automation.user_id == as_uuid(user.id))).first()
    if not a:
        raise NotFound("Automation not found")
    return a


@automations_router.patch("/{automation_id}", response_model=AutomationOut)
def update_automation(automation_id: str, body: AutomationUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _own(db, user, automation_id)
    if body.is_enabled is not None:
        a.is_enabled = body.is_enabled
    if body.configuration is not None:
        a.configuration = {**(a.configuration or {}), **body.configuration}
    audit(db, user.id, "automation.update", "automation", a.id, {"enabled": a.is_enabled})
    return a


@automations_router.post("/{automation_id}/run", dependencies=[Depends(rate_limit("automation-run", 10))])
def run_automation(automation_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _own(db, user, automation_id)
    return {"result": auto.run_automation(db, a), "last_executed_at": datetime.now(timezone.utc).isoformat()}


# ------------------------------------------------------------------ settings
@settings_router.get("")
def get_user_settings(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                      paypal: PayPalClient = Depends(get_paypal)):
    profile = db.get(Profile, as_uuid(user.id))
    return {"profile": ProfileOut.model_validate(profile).model_dump(mode="json"),
            "integrations": {"paypal": paypal_status(settings, paypal, db), "razorpay": {"configured": settings.razorpay_configured, "mode": "test" if settings.razorpay_key_id.startswith("rzp_test") else "live"},
                             "ai": {"provider": settings.ai_provider, "configured": bool(settings.gemini_api_key if settings.ai_provider == "gemini" else settings.openai_api_key)}}}


@settings_router.patch("/profile", response_model=ProfileOut)
def update_profile(body: ProfileUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Profile, as_uuid(user.id))
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    audit(db, user.id, "profile.update", "profile", user.id)
    return p
