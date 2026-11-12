from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from decimal import Decimal
import uuid

from sqlalchemy.orm import Session

from app.api.deps import get_paypal, get_payment_service
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.ratelimit import ip_rate_limit, user_rate_limit
from app.core.security import CurrentUser, get_current_user
from app.models import PaymentLink
from app.payments.paypal.client import PayPalClient
from app.api.routes.core import paypal_status
from app.schemas.entities import PaymentLinkOut, TransactionOut, SUPPORTED_CURRENCIES
from app.services.payments import PaymentService

paypal_router = APIRouter(prefix="/api/payments/paypal", tags=["paypal"])
razorpay_router = APIRouter(prefix="/api/payments/razorpay", tags=["razorpay"])
public_router = APIRouter(prefix="/api/public/pay", tags=["public-checkout"])


class CreateOrderIn(BaseModel):
    invoice_id: uuid.UUID | None = None
    amount: Decimal | None = Field(None, gt=0, le=Decimal("100000000"))
    currency: str | None = None
    description: str | None = Field(None, max_length=500)
    customer_id: uuid.UUID | None = None

    @field_validator("currency")
    @classmethod
    def _c(cls, v):
        if v and v.upper() not in SUPPORTED_CURRENCIES:
            raise ValueError("Unsupported currency")
        return v.upper() if v else v


class OrderRefIn(BaseModel):
    order_id: str = Field(min_length=3, max_length=100)


class RazorpayVerifyIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def _order_response(txn, link: PaymentLink) -> dict:
    return {"transaction": TransactionOut.model_validate(txn).model_dump(mode="json"), "order_id": txn.provider_order_id,
            "approval_url": link.url if txn.provider == "paypal" else None, "checkout_url": link.url,
            "payment_link": PaymentLinkOut.model_validate(link).model_dump(mode="json")}


# ------------------------------------------------------------------ PayPal (authenticated)
@paypal_router.post("/create-order", status_code=201, dependencies=[Depends(user_rate_limit("pay-create", 20))])
def paypal_create_order(body: CreateOrderIn, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service)):
    txn, link = payments.create_paypal_order(user.id, invoice_id=body.invoice_id, amount=body.amount, currency=body.currency,
                                             description=body.description, customer_id=body.customer_id)
    return _order_response(txn, link)


@paypal_router.post("/capture-order", dependencies=[Depends(user_rate_limit("pay-capture", 20))])
def paypal_capture(body: OrderRefIn, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service)):
    txn = payments.capture_paypal_order(body.order_id, user_id=user.id)  # 404 if the order is not the caller's
    return {"status": txn.status, "transaction": TransactionOut.model_validate(txn).model_dump(mode="json")}


@paypal_router.get("/order/{order_id}")
def paypal_order(order_id: str, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service)):
    txn, order = payments.reconcile_paypal_order(order_id, user_id=user.id)
    return {"paypal_status": order.get("status"), "transaction": TransactionOut.model_validate(txn).model_dump(mode="json")}


@paypal_router.get("/status")
def paypal_connection(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                      paypal: PayPalClient = Depends(get_paypal)):
    return paypal_status(settings, paypal, db)


# ------------------------------------------------------------------ Razorpay (authenticated)
@razorpay_router.post("/create-order", status_code=201, dependencies=[Depends(user_rate_limit("pay-create", 20))])
def razorpay_create_order(body: CreateOrderIn, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service),
                          settings: Settings = Depends(get_settings)):
    txn, link = payments.create_razorpay_order(user.id, invoice_id=body.invoice_id, amount=body.amount, currency=body.currency,
                                               description=body.description, customer_id=body.customer_id)
    return {**_order_response(txn, link), "key_id": settings.razorpay_key_id, "amount_paise": int(txn.amount * 100)}


@razorpay_router.post("/verify-payment", dependencies=[Depends(user_rate_limit("pay-capture", 20))])
def razorpay_verify(body: RazorpayVerifyIn, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service)):
    txn = payments.verify_razorpay_payment(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature, user_id=user.id)
    return {"status": txn.status, "transaction": TransactionOut.model_validate(txn).model_dump(mode="json")}


# ------------------------------------------------------------------ public checkout (customer-facing, no login)
# Safe without auth: PayPal only lets a capture succeed for an order the payer approved, Razorpay needs a valid HMAC,
# and every outcome is re-verified with the provider and amount-checked against the DB.
def _public(txn) -> dict:
    return {"status": txn.status, "amount": float(txn.amount), "currency": txn.currency, "invoice_number": txn.invoice.invoice_number if txn.invoice else None}


@public_router.get("/link/{link_id}", dependencies=[Depends(ip_rate_limit("public", 60))])
def public_link(link_id: str, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    from app.core.errors import NotFound
    from app.models import Profile
    try:
        link = db.get(PaymentLink, uuid.UUID(link_id))
    except ValueError:
        link = None
    if not link:
        raise NotFound("Payment link not found")
    merchant = db.get(Profile, link.user_id)
    return {"provider": link.provider, "amount": float(link.amount), "currency": link.currency, "description": link.description, "status": link.status,
            "merchant": (merchant.business_name or merchant.full_name) if merchant else None, "order_id": link.provider_reference,
            "key_id": settings.razorpay_key_id if link.provider == "razorpay" else None, "url": link.url if link.provider == "paypal" else None}


@public_router.post("/paypal/capture", dependencies=[Depends(ip_rate_limit("public", 20))])
def public_paypal_capture(body: OrderRefIn, payments: PaymentService = Depends(get_payment_service)):
    return _public(payments.capture_paypal_order(body.order_id))


@public_router.post("/paypal/cancel", dependencies=[Depends(ip_rate_limit("public", 20))])
def public_paypal_cancel(body: OrderRefIn, payments: PaymentService = Depends(get_payment_service)):
    return _public(payments.mark_cancelled("paypal", body.order_id))


@public_router.post("/razorpay/verify", dependencies=[Depends(ip_rate_limit("public", 20))])
def public_razorpay_verify(body: RazorpayVerifyIn, payments: PaymentService = Depends(get_payment_service)):
    return _public(payments.verify_razorpay_payment(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature))
