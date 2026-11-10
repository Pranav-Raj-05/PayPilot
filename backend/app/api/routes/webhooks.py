"""Provider webhooks. Authenticity is verified BEFORE anything is read or written; deliveries are de-duplicated so
provider retries are safe; a processing failure rolls everything back (incl. the dedupe row) so the retry reprocesses."""
import json
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_paypal, get_payment_service
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.errors import AppError, NotFound, Unauthorized
from app.models import Transaction, WebhookEvent
from app.payments.paypal.client import PayPalClient, parse_capture
from app.payments.base import CaptureResult
from app.payments.razorpay.client import verify_webhook_signature
from app.services.payments import PaymentService
from app.services.transactions import add_event
from sqlalchemy import select

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])
log = logging.getLogger("paypilot.webhooks")


def _dedupe(db: Session, provider: str, event_id: str, event_type: str, payload: dict) -> bool:
    """True if this is the first time we see the event."""
    try:
        with db.begin_nested():
            db.add(WebhookEvent(provider=provider, event_id=event_id, event_type=event_type, payload={"type": event_type, "id": event_id}))
            db.flush()
        return True
    except IntegrityError:
        return False


@router.post("/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db), paypal: PayPalClient = Depends(get_paypal),
                         payments: PaymentService = Depends(get_payment_service)):
    raw = await request.body()
    try:
        event = json.loads(raw)
    except ValueError:
        raise AppError("Invalid JSON", code="invalid_payload")
    if not paypal.verify_webhook(dict(request.headers), event):
        log.warning("paypal webhook signature verification failed")
        raise Unauthorized("Webhook verification failed")
    etype, eid = event.get("event_type", ""), event.get("id", "")
    if not _dedupe(db, "paypal", eid, etype, event):
        return {"received": True, "duplicate": True}
    res = event.get("resource", {}) or {}
    order_id = ((res.get("supplemental_data") or {}).get("related_ids") or {}).get("order_id") or (res.get("id") if etype.startswith("CHECKOUT.ORDER") else None)

    def find_txn() -> Transaction | None:
        if order_id:
            return db.scalars(select(Transaction).where(Transaction.provider == "paypal", Transaction.provider_order_id == order_id).with_for_update()).unique().first()
        up = next((l["href"] for l in res.get("links", []) if l.get("rel") == "up"), "")
        cap_id = res.get("id") if etype.startswith("PAYMENT.CAPTURE.COMPLETED") else up.rstrip("/").split("/")[-1]
        return db.scalars(select(Transaction).where(Transaction.provider == "paypal", Transaction.provider_capture_id == cap_id).with_for_update()).unique().first()

    txn = find_txn()
    if txn is None:
        return {"received": True, "matched": False}  # not ours (or created elsewhere) - acknowledge so PayPal stops retrying
    if etype == "CHECKOUT.ORDER.APPROVED":
        add_event(txn, "approved", "Customer approved the order at PayPal")
        if txn.status == "created":
            txn.status = "pending"
    elif etype == "PAYMENT.CAPTURE.COMPLETED":
        order = paypal.get_order(txn.provider_order_id)  # never trust the webhook body alone: re-verify with PayPal
        payments.apply_capture(txn, parse_capture(order))
    elif etype in ("PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"):
        payments.apply_capture(txn, CaptureResult(status="failed", order_id=txn.provider_order_id or "", reason="Capture denied by PayPal"))
    elif etype in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        payments.apply_refund(txn)
    return {"received": True, "matched": True, "type": etype}


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                           payments: PaymentService = Depends(get_payment_service)):
    raw = await request.body()
    if not verify_webhook_signature(raw, request.headers.get("x-razorpay-signature", ""), settings.razorpay_webhook_secret):
        raise Unauthorized("Webhook verification failed")
    event = json.loads(raw)
    eid = request.headers.get("x-razorpay-event-id") or f"{event.get('event')}:{(event.get('payload', {}).get('payment', {}).get('entity', {}) or {}).get('id')}"
    if not _dedupe(db, "razorpay", eid, event.get("event", ""), event):
        return {"received": True, "duplicate": True}
    txn = payments.razorpay_webhook_event(event)
    return {"received": True, "matched": txn is not None}
