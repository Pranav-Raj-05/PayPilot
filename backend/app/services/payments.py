"""Payment orchestration. Rules enforced here:
- amounts/currency come from the DB (invoice), never from the client, when an invoice is involved
- success is only ever recorded from a provider-verified capture
- capture is idempotent (DB row lock + provider idempotency key + status check)
- an invoice becomes paid ONLY after a verified, amount-matching capture
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, Conflict, NotFound
from app.models import Invoice, PaymentLink, Transaction
from app.payments.base import CaptureResult
from app.payments.paypal.client import PayPalClient
from app.payments.razorpay.client import RazorpayClient, verify_payment_signature
from app.repositories import CustomerRepo, InvoiceRepo, PaymentLinkRepo, TransactionRepo
from app.services.common import as_uuid, aware, audit, notify
from app.services.transactions import add_event

log = logging.getLogger("paypilot.payments")
FINAL = {"completed", "refunded"}


class PaymentService:
    def __init__(self, db: Session, settings: Settings, paypal: PayPalClient, razorpay: RazorpayClient):
        self.db, self.settings, self.paypal, self.razorpay = db, settings, paypal, razorpay

    # ------------------------------------------------------------------ helpers
    def _txn_by_order(self, provider: str, order_id: str, user_id=None, lock: bool = False) -> Transaction:
        q = select(Transaction).where(Transaction.provider == provider, Transaction.provider_order_id == order_id)
        if user_id is not None:
            q = q.where(Transaction.user_id == as_uuid(user_id))
        if lock:
            q = q.with_for_update()
        txn = self.db.scalars(q).unique().first()
        if txn is None:
            raise NotFound("Order not found")  # same answer for "not yours" - no existence leak
        return txn

    def _resolve_amount(self, user_id, invoice_id, amount, currency, customer_id):
        """Returns (invoice|None, amount, currency, customer_id). Invoice values override anything client-supplied."""
        if invoice_id:
            inv = InvoiceRepo(self.db, user_id).get(invoice_id)
            if inv.status == "paid":
                raise Conflict("Invoice is already paid")
            if inv.status == "cancelled":
                raise Conflict("Invoice is cancelled")
            if inv.total_amount <= 0:
                raise AppError("Invoice total must be greater than zero", code="invalid_amount")
            return inv, Decimal(inv.total_amount), inv.currency, inv.customer_id
        if amount is None or currency is None:
            raise AppError("amount and currency are required when no invoice is given", code="invalid_amount")
        if Decimal(amount) <= 0:
            raise AppError("Amount must be greater than zero", code="invalid_amount")
        if customer_id:
            CustomerRepo(self.db, user_id).get(customer_id)
        return None, Decimal(amount), currency, customer_id

    # ------------------------------------------------------------------ PayPal
    def create_paypal_order(self, user_id, *, invoice_id=None, amount=None, currency=None, description=None,
                            customer_id=None, expires_at: datetime | None = None) -> tuple[Transaction, PaymentLink]:
        inv, amount, currency, customer_id = self._resolve_amount(user_id, invoice_id, amount, currency, customer_id)
        if inv is not None and inv.payment_provider == "paypal" and inv.payment_link:
            existing = self.db.scalars(select(Transaction).where(
                Transaction.invoice_id == inv.id, Transaction.provider == "paypal",
                Transaction.status.in_(["created", "pending"]))).unique().first()
            if existing:  # reuse the open order instead of spawning duplicates
                link = self.db.scalars(select(PaymentLink).where(PaymentLink.invoice_id == inv.id, PaymentLink.provider == "paypal",
                                                                 PaymentLink.status == "active")).first()
                if link:
                    return existing, link
        description = description or (f"Invoice {inv.invoice_number}" if inv else "PayPilot payment")
        txn_id = uuid.uuid4()
        front = self.settings.frontend_url.rstrip("/")
        order = self.paypal.create_order(
            amount=amount, currency=currency, reference_id=inv.invoice_number if inv else str(txn_id), custom_id=str(txn_id),
            description=description, return_url=f"{front}/pay/return?provider=paypal", cancel_url=f"{front}/pay/cancelled?provider=paypal",
            request_id=str(txn_id))
        if not order.approval_url:
            raise AppError("PayPal did not return an approval URL", code="provider_error", status_code=502)
        txn = Transaction(id=txn_id, user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None,
                          provider="paypal", provider_order_id=order.order_id, amount=amount, currency=currency,
                          status="created", events=[])
        add_event(txn, "order_created", f"PayPal order {order.order_id}")
        link = PaymentLink(user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None, provider="paypal",
                           provider_reference=order.order_id, url=order.approval_url, amount=amount, currency=currency,
                           description=description, status="active",
                           expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(days=3)))
        self.db.add_all([txn, link])
        self.db.flush()
        txn.payment_link_id = link.id
        if inv is not None:
            inv.payment_provider, inv.payment_link = "paypal", order.approval_url
            if inv.status == "draft":
                inv.status = "pending"
        audit(self.db, user_id, "paypal.order_created", "transaction", txn.id, {"order_id": order.order_id, "amount": str(amount), "currency": currency})
        return txn, link

    def capture_paypal_order(self, order_id: str, user_id=None) -> Transaction:
        txn = self._txn_by_order("paypal", order_id, user_id, lock=True)
        if txn.status in FINAL:
            return txn  # idempotent replay
        self._assert_payable(txn)
        result = self.paypal.capture_order(order_id, request_id=f"capture-{txn.id}")
        self.apply_capture(txn, result)
        return txn

    def _assert_payable(self, txn: Transaction) -> None:
        if txn.status == "cancelled":
            raise Conflict("This payment was cancelled")
        if txn.payment_link_id:
            link = self.db.get(PaymentLink, txn.payment_link_id)
            if link and link.status == "cancelled":
                raise Conflict("This payment link was cancelled")
            exp = aware(link.expires_at) if link else None
            if link and exp and exp < datetime.now(timezone.utc):
                link.status = "expired"
                raise Conflict("This payment link has expired")

    def reconcile_paypal_order(self, order_id: str, user_id=None) -> tuple[Transaction, dict]:
        """GET order from PayPal; if PayPal says it is captured but we have not recorded it, record it (verified)."""
        txn = self._txn_by_order("paypal", order_id, user_id, lock=True)
        order = self.paypal.get_order(order_id)
        if txn.status not in FINAL and order.get("status") == "COMPLETED":
            from app.payments.paypal.client import parse_capture
            self.apply_capture(txn, parse_capture(order))
        return txn, order

    def mark_cancelled(self, provider: str, order_id: str) -> Transaction:
        txn = self._txn_by_order(provider, order_id, lock=True)
        if txn.status == "created":
            txn.status = "cancelled"
            add_event(txn, "cancelled", "Customer cancelled at checkout")
            self._close_link(txn, "cancelled")
        return txn

    # ------------------------------------------------------------------ shared outcome handling
    def apply_capture(self, txn: Transaction, result: CaptureResult) -> None:
        if txn.status in FINAL:
            return
        if result.status == "completed":
            if result.amount is None or Decimal(result.amount) != Decimal(txn.amount) or result.currency != txn.currency:
                txn.status, txn.failure_reason = "failed", "Captured amount/currency did not match the order"
                add_event(txn, "amount_mismatch", f"expected {txn.amount} {txn.currency}, got {result.amount} {result.currency}")
                notify(self.db, txn.user_id, "Payment flagged", "A captured payment did not match the expected amount.", "alert", {"transaction_id": str(txn.id)})
                audit(self.db, txn.user_id, "payment.amount_mismatch", "transaction", txn.id)
                return
            txn.status, txn.provider_capture_id = "completed", result.capture_id
            txn.payment_method = result.payment_method or txn.payment_method
            txn.failure_reason = None
            add_event(txn, "captured", f"Capture {result.capture_id}")
            if txn.invoice_id:
                inv = self.db.get(Invoice, txn.invoice_id)
                if inv and inv.status != "paid":
                    inv.status = "paid"
                    add_event(txn, "invoice_paid", inv.invoice_number)
            self._close_link(txn, "paid")
            notify(self.db, txn.user_id, "Payment received", f"{txn.amount} {txn.currency} received via {txn.provider}.", "success", {"transaction_id": str(txn.id)})
            audit(self.db, txn.user_id, "payment.completed", "transaction", txn.id, {"provider": txn.provider})
        elif result.status == "pending":
            txn.status = "pending"
            add_event(txn, "pending", result.reason)
        else:
            txn.status, txn.failure_reason = "failed", result.reason or "Payment failed"
            add_event(txn, "failed", txn.failure_reason)
            notify(self.db, txn.user_id, "Payment failed", txn.failure_reason, "alert", {"transaction_id": str(txn.id)})
            audit(self.db, txn.user_id, "payment.failed", "transaction", txn.id)

    def apply_refund(self, txn: Transaction) -> None:
        if txn.status == "refunded":
            return
        txn.status = "refunded"
        add_event(txn, "refunded")
        if txn.invoice_id:
            inv = self.db.get(Invoice, txn.invoice_id)
            if inv and inv.status == "paid":
                inv.status = "pending"
        notify(self.db, txn.user_id, "Payment refunded", f"{txn.amount} {txn.currency} was refunded.", "warning", {"transaction_id": str(txn.id)})

    def _close_link(self, txn: Transaction, status: str) -> None:
        if txn.payment_link_id:
            link = self.db.get(PaymentLink, txn.payment_link_id)
            if link and link.status == "active":
                link.status = status

    # ------------------------------------------------------------------ Razorpay
    def create_razorpay_order(self, user_id, *, invoice_id=None, amount=None, currency=None, description=None,
                              customer_id=None, expires_at=None) -> tuple[Transaction, PaymentLink]:
        inv, amount, currency, customer_id = self._resolve_amount(user_id, invoice_id, amount, currency, customer_id)
        if currency != "INR":
            raise AppError("Razorpay supports INR in this integration", code="unsupported_currency")
        txn_id = uuid.uuid4()
        order = self.razorpay.create_order(amount, currency, receipt=inv.invoice_number if inv else str(txn_id), notes={"transaction_id": str(txn_id)})
        description = description or (f"Invoice {inv.invoice_number}" if inv else "PayPilot payment")
        txn = Transaction(id=txn_id, user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None,
                          provider="razorpay", provider_order_id=order["id"], amount=amount, currency=currency, status="created", events=[])
        add_event(txn, "order_created", f"Razorpay order {order['id']}")
        link_id = uuid.uuid4()
        link = PaymentLink(id=link_id, user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None,
                           provider="razorpay", provider_reference=order["id"],
                           url=f"{self.settings.frontend_url.rstrip('/')}/pay/razorpay/{link_id}", amount=amount, currency=currency,
                           description=description, status="active", expires_at=expires_at)
        self.db.add_all([txn, link])
        self.db.flush()
        txn.payment_link_id = link.id
        if inv is not None:
            inv.payment_provider, inv.payment_link = "razorpay", link.url
            if inv.status == "draft":
                inv.status = "pending"
        audit(self.db, user_id, "razorpay.order_created", "transaction", txn.id, {"order_id": order["id"]})
        return txn, link

    def verify_razorpay_payment(self, order_id: str, payment_id: str, signature: str, user_id=None) -> Transaction:
        txn = self._txn_by_order("razorpay", order_id, user_id, lock=True)
        if txn.status in FINAL:
            return txn
        if not verify_payment_signature(order_id, payment_id, signature, self.settings.razorpay_key_secret):
            add_event(txn, "signature_invalid")
            audit(self.db, txn.user_id, "razorpay.bad_signature", "transaction", txn.id)
            raise AppError("Payment signature verification failed", code="invalid_signature", status_code=400)
        self._apply_razorpay_payment(txn, self.razorpay.get_payment(payment_id))  # confirm with Razorpay itself
        return txn

    def _apply_razorpay_payment(self, txn: Transaction, payment: dict) -> None:
        status = payment.get("status")
        amount = Decimal(payment.get("amount", 0)) / 100
        if payment.get("order_id") != txn.provider_order_id:
            raise AppError("Payment does not belong to this order", code="order_mismatch", status_code=400)
        mapped = "completed" if status == "captured" else "failed" if status == "failed" else "pending"
        self.apply_capture(txn, CaptureResult(status=mapped, order_id=txn.provider_order_id or "", capture_id=payment.get("id"), amount=amount,
                                              currency=(payment.get("currency") or "").upper(), payment_method=payment.get("method"),
                                              reason=payment.get("error_description")))

    def razorpay_webhook_event(self, event: dict) -> Transaction | None:
        etype = event.get("event", "")
        payment = (event.get("payload", {}).get("payment", {}) or {}).get("entity")
        if not payment or etype not in ("payment.captured", "payment.failed"):
            return None
        order_id = payment.get("order_id")
        if not order_id:
            return None
        try:
            txn = self._txn_by_order("razorpay", order_id, lock=True)
        except NotFound:
            return None
        if txn.status not in FINAL:
            self._apply_razorpay_payment(txn, payment)
        return txn
