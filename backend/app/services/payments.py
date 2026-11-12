"""Payment orchestration. Rules enforced here:
- amounts/currency come from the DB (invoice), never from the client, when an invoice is involved
- success is only ever recorded from a provider-verified capture
- capture is idempotent (DB row lock + provider idempotency key + status check)
- an invoice becomes paid ONLY after a verified, amount-matching capture for the CURRENT version of that invoice
  (a cancelled, changed or already-paid invoice is never marked paid; a payment that still arrives is recorded as
  money received and flagged for the merchant instead)
- refunds are stored one row each; the payment keeps its original amount and a cumulative refunded amount
"""
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, Conflict, NotFound, ProviderError
from app.models import Invoice, PaymentLink, Refund, Transaction
from app.payments.base import CaptureResult, RefundResult
from app.payments.paypal.client import PayPalClient, parse_capture
from app.payments.razorpay.client import RazorpayClient, verify_payment_signature
from app.repositories import CustomerRepo, InvoiceRepo, PaymentLinkRepo, TransactionRepo
from app.services.common import as_uuid, aware, audit, notify, notify_payment
from app.services.invoices import q2
from app.services.orders import OPEN_STATUSES, invoice_problem, live_orders, void_order
from app.services.transactions import add_event

log = logging.getLogger("paypilot.payments")
FINAL = {"completed", "partially_refunded", "refunded"}  # payments that settled (money was captured)


class PaymentService:
    def __init__(self, db: Session, settings: Settings, paypal: PayPalClient, razorpay: RazorpayClient):
        self.db, self.settings, self.paypal, self.razorpay = db, settings, paypal, razorpay
        # provider -> callable(txn, amount, request_id) -> RefundResult. Razorpay joins this map when its refund API is integrated;
        # everything downstream (record_refund, totals, statuses, webhooks) is provider-agnostic.
        self._refunders = {"paypal": self._paypal_refund}

    # ------------------------------------------------------------------ helpers
    def _txn_by_order(self, provider: str, order_id: str, user_id=None, lock: bool = False) -> Transaction:
        q = select(Transaction).where(Transaction.provider == provider, Transaction.provider_order_id == order_id)
        if user_id is not None:
            q = q.where(Transaction.user_id == as_uuid(user_id))
        if lock:
            q = q.with_for_update(of=Transaction)
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

    def _reuse_live_order(self, inv: Invoice, provider: str) -> tuple[Transaction, PaymentLink] | None:
        """One live order per invoice. A matching live order from the same provider is reused; one that no longer matches the
        invoice (legacy data) is voided; a live order from the OTHER provider must be cancelled by the merchant first, so a
        customer can never hold two payable links for one invoice."""
        for txn in live_orders(self.db, inv.id, lock=True):
            stale = (txn.invoice_version is not None and txn.invoice_version != inv.version) or \
                Decimal(txn.amount) != Decimal(inv.total_amount) or txn.currency != inv.currency
            if stale:
                void_order(self.db, txn, "Invoice changed; this link was replaced")
                continue
            if txn.provider != provider:
                raise Conflict(f"This invoice already has an active {txn.provider} payment link. Cancel it before creating a {provider} link.")
            link = self.db.get(PaymentLink, txn.payment_link_id) if txn.payment_link_id else None
            if link is not None:
                return txn, link
        return None

    # ------------------------------------------------------------------ PayPal
    def create_paypal_order(self, user_id, *, invoice_id=None, amount=None, currency=None, description=None,
                            customer_id=None, expires_at: datetime | None = None) -> tuple[Transaction, PaymentLink]:
        inv, amount, currency, customer_id = self._resolve_amount(user_id, invoice_id, amount, currency, customer_id)
        if inv is not None:
            reused = self._reuse_live_order(inv, "paypal")
            if reused is not None:
                return reused
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
                          status="created", events=[], invoice_version=inv.version if inv else None)
        add_event(txn, "order_created", f"PayPal order {order.order_id}")
        link = PaymentLink(user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None, provider="paypal",
                           provider_reference=order.order_id, url=order.approval_url, amount=amount, currency=currency,
                           description=description, status="active", invoice_version=inv.version if inv else None,
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
        self._assert_payable(txn, check_invoice=True)  # refuse BEFORE any money moves
        result = self.paypal.capture_order(order_id, request_id=f"capture-{txn.id}")
        self.apply_capture(txn, result)
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
            # The money really moved, so the transaction is recorded as completed whatever happens to the invoice.
            txn.status, txn.provider_capture_id = "completed", result.capture_id
            txn.payment_method = result.payment_method or txn.payment_method
            txn.failure_reason = None
            txn.fee_amount, txn.net_amount = result.fee, result.net
            add_event(txn, "captured", f"Capture {result.capture_id}")
            inv = self.db.get(Invoice, txn.invoice_id, with_for_update={"of": Invoice}) if txn.invoice_id else None
            if inv is not None:
                problem = invoice_problem(inv, txn)
                if problem is None:
                    inv.status = "paid"
                    add_event(txn, "invoice_paid", inv.invoice_number)
                else:  # never pay an invoice with a payment that was not made for its current state
                    add_event(txn, "payment_unmatched", f"Invoice {inv.invoice_number} was not marked paid: {problem}")
                    notify(self.db, txn.user_id, "Payment needs review",
                           f"{txn.amount} {txn.currency} was received but invoice {inv.invoice_number} was not marked paid: {problem}. "
                           f"Review the transaction and refund it if it is not owed.", "alert", {"transaction_id": str(txn.id), "invoice_id": str(inv.id)})
                    audit(self.db, txn.user_id, "payment.unmatched", "transaction", txn.id, {"reason": problem})
            self._close_link(txn, "paid")
            notify_payment(self.db, txn.user_id, "Payment received", f"{txn.amount} {txn.currency} received via {txn.provider}.", "success", {"transaction_id": str(txn.id)})
            audit(self.db, txn.user_id, "payment.completed", "transaction", txn.id, {"provider": txn.provider})
        elif result.status == "pending":
            txn.status = "pending"
            add_event(txn, "pending", result.reason)
        else:
            txn.status, txn.failure_reason = "failed", result.reason or "Payment failed"
            add_event(txn, "failed", txn.failure_reason)
            notify_payment(self.db, txn.user_id, "Payment failed", txn.failure_reason, "alert", {"transaction_id": str(txn.id)})
            audit(self.db, txn.user_id, "payment.failed", "transaction", txn.id)

    # ------------------------------------------------------------------ refunds (provider-agnostic core)
    def _refund_totals(self, txn: Transaction) -> tuple[Decimal, Decimal]:
        """(completed, pending) refund amounts for a payment."""
        done = sum((Decimal(r.amount) for r in txn.refunds if r.status == "completed"), Decimal(0))
        pending = sum((Decimal(r.amount) for r in txn.refunds if r.status == "pending"), Decimal(0))
        return q2(done), q2(pending)

    def refundable_remaining(self, txn: Transaction) -> Decimal:
        done, pending = self._refund_totals(txn)
        return q2(Decimal(txn.amount) - done - pending)

    def _refund_anomaly(self, txn: Transaction, kind: str, detail: str) -> None:
        """A refund event that does not add up (wrong currency, more than was paid, ...): never applied, always surfaced."""
        add_event(txn, kind, detail)
        notify(self.db, txn.user_id, "Refund needs review", f"A refund could not be applied automatically: {detail}.", "alert", {"transaction_id": str(txn.id)})
        audit(self.db, txn.user_id, f"refund.{kind}", "transaction", txn.id, {"detail": detail})
        return None

    def record_refund(self, txn: Transaction, *, provider_refund_id: str, amount, currency: str | None, status: str, source: str,
                      reason: str | None = None, idempotency_key: str | None = None) -> Refund | None:
        """Single entry point for every refund (our own API call or a provider webhook). Idempotent per provider refund id, so
        duplicate or out-of-order deliveries never count a refund twice. The caller must hold the transaction row lock."""
        if not provider_refund_id:
            raise ProviderError("The provider did not return a refund id")
        amount = q2(Decimal(amount))
        existing = next((r for r in txn.refunds if r.provider_refund_id == provider_refund_id), None) or self.db.scalars(
            select(Refund).where(Refund.provider == txn.provider, Refund.provider_refund_id == provider_refund_id)).first()
        if existing is not None:
            if existing.transaction_id != txn.id:
                return self._refund_anomaly(txn, "refund_id_conflict", f"refund {provider_refund_id} is recorded against a different payment")
            if existing.status == "pending" and status in ("completed", "failed"):  # a pending refund settling
                if amount != Decimal(existing.amount) or currency != existing.currency:
                    return self._refund_anomaly(txn, "refund_amount_mismatch", f"refund {provider_refund_id} settled as {amount} {currency}, expected {existing.amount} {existing.currency}")
                existing.status = status
                self._after_refund_change(txn, existing)
            return existing  # a replay: nothing changes
        if currency != txn.currency:
            return self._refund_anomaly(txn, "refund_currency_mismatch", f"refund {provider_refund_id} is in {currency}, the payment is in {txn.currency}")
        if amount <= 0:
            return self._refund_anomaly(txn, "refund_amount_invalid", f"refund {provider_refund_id} has a non-positive amount")
        done, pending = self._refund_totals(txn)
        total = Decimal(txn.amount)
        if (status == "completed" and done + amount > total) or (status == "pending" and done + pending + amount > total):
            return self._refund_anomaly(txn, "refund_exceeds_payment", f"refund {provider_refund_id} of {amount} {currency} would exceed the {total} {currency} paid")
        row = Refund(user_id=txn.user_id, transaction_id=txn.id, provider=txn.provider, provider_refund_id=provider_refund_id, amount=amount,
                     currency=currency, status=status, source=source, reason=reason, idempotency_key=idempotency_key)
        txn.refunds.append(row)
        self.db.flush()
        self._after_refund_change(txn, row)
        return row

    def _after_refund_change(self, txn: Transaction, row: Refund) -> None:
        done, _ = self._refund_totals(txn)
        txn.refunded_amount = done
        detail = f"{row.provider_refund_id}: {row.amount} {row.currency}"
        if row.status == "completed":
            add_event(txn, "refund_completed", detail)
            if txn.status in ("completed", "partially_refunded"):
                if done >= Decimal(txn.amount):
                    txn.status = "refunded"
                    add_event(txn, "refunded", f"{done} {txn.currency} refunded in total")
                    inv = self.db.get(Invoice, txn.invoice_id) if txn.invoice_id else None
                    if inv and inv.status == "paid":  # only a FULL refund puts the invoice back on the table
                        inv.status = "pending"
                    notify_payment(self.db, txn.user_id, "Payment refunded", f"{txn.amount} {txn.currency} was refunded.", "warning", {"transaction_id": str(txn.id)})
                elif done > 0:
                    txn.status = "partially_refunded"
                    notify_payment(self.db, txn.user_id, "Payment partially refunded", f"{row.amount} {row.currency} of {txn.amount} {txn.currency} was refunded.",
                                   "warning", {"transaction_id": str(txn.id)})
            audit(self.db, txn.user_id, "refund.completed", "transaction", txn.id, {"refund": row.provider_refund_id, "amount": str(row.amount)})
        elif row.status == "pending":
            add_event(txn, "refund_pending", detail)
        else:
            add_event(txn, "refund_failed", detail)
            audit(self.db, txn.user_id, "refund.failed", "transaction", txn.id, {"refund": row.provider_refund_id})

    def _ensure_settled(self, txn: Transaction) -> None:
        """Webhooks can arrive out of order. A refund event for a payment we have not recorded as captured yet first reconciles the
        payment with the provider; if it still is not settled the event is rejected so the provider retries it later."""
        if txn.status in ("created", "pending") and txn.provider == "paypal" and txn.provider_order_id:
            self.apply_capture(txn, parse_capture(self.paypal.get_order(txn.provider_order_id)))
        if txn.status not in FINAL:
            raise Conflict("The payment is not settled yet; this refund event will be retried")

    def apply_provider_refund(self, txn: Transaction, result: RefundResult, source: str = "webhook") -> Refund | None:
        """A refund the provider reports (already read back from the provider, not taken from a webhook body)."""
        if result.amount is None:
            raise ProviderError("The provider did not report a refund amount")
        self._ensure_settled(txn)
        return self.record_refund(txn, provider_refund_id=result.provider_refund_id, amount=result.amount, currency=result.currency,
                                  status=result.status, source=source)

    def apply_reversal(self, txn: Transaction) -> Refund | None:
        """The provider reversed the whole capture (e.g. a lost dispute): recorded as one refund of whatever was still outstanding."""
        self._ensure_settled(txn)
        remaining = q2(Decimal(txn.amount) - self._refund_totals(txn)[0])
        if remaining <= 0:
            return None
        return self.record_refund(txn, provider_refund_id=f"reversal-{txn.provider_capture_id}", amount=remaining, currency=txn.currency,
                                  status="completed", source="webhook", reason="Capture reversed by the provider")

    def _paypal_refund(self, txn: Transaction, amount: Decimal, request_id: str) -> RefundResult:
        return self.paypal.refund_capture(txn.provider_capture_id, request_id, amount=amount, currency=txn.currency)

    def refund(self, txn_id, user_id, *, amount=None, idempotency_key: str | None = None, reason: str | None = None) -> Transaction:
        """Refund all of what is left (amount=None) or part of it. Safe to repeat: the row is locked, outstanding refunds are
        counted, a repeated idempotency key returns the first result, and the provider call carries its own idempotency key."""
        txn = TransactionRepo(self.db, user_id).get(txn_id)
        txn = self.db.scalars(select(Transaction).where(Transaction.id == txn.id).with_for_update(of=Transaction)).unique().one()
        refunder = self._refunders.get(txn.provider)
        if refunder is None:
            raise AppError("Refunds are only supported for PayPal payments here", code="unsupported", status_code=400)
        if idempotency_key and any(r.idempotency_key == idempotency_key for r in txn.refunds):
            return txn
        if txn.status == "refunded":
            return txn
        if txn.status not in ("completed", "partially_refunded") or not txn.provider_capture_id:
            raise Conflict("Only completed payments can be refunded")
        remaining = self.refundable_remaining(txn)
        if remaining <= 0:
            if amount is None:
                return txn  # everything is already refunded or has a refund in flight
            raise Conflict("Nothing is left to refund on this payment (a refund may still be pending)")
        amt = remaining if amount is None else q2(Decimal(amount))
        if amt <= 0 or amt > remaining:
            raise AppError(f"Refund amount must be between 0.01 and {remaining} {txn.currency}", code="invalid_amount", status_code=422)
        done, _ = self._refund_totals(txn)
        intent = f"{txn.id}:{idempotency_key}" if idempotency_key else f"{txn.id}:{int(done * 100)}:{int(amt * 100)}"
        request_id = "refund-" + hashlib.sha256(intent.encode()).hexdigest()[:40]  # same intent => same provider idempotency key
        result = refunder(txn, amt, request_id)
        if result.status == "failed":
            raise ProviderError("The provider could not complete the refund")
        self.record_refund(txn, provider_refund_id=result.provider_refund_id, amount=result.amount or amt, currency=result.currency or txn.currency,
                           status=result.status, source="api", reason=reason, idempotency_key=idempotency_key)
        audit(self.db, user_id, f"{txn.provider}.refund", "transaction", txn.id, {"status": result.status, "amount": str(amt)})
        return txn

    def _assert_payable(self, txn: Transaction, check_invoice: bool = False) -> None:
        if txn.status == "cancelled":
            raise Conflict("This payment was cancelled")
        link = self.db.get(PaymentLink, txn.payment_link_id) if txn.payment_link_id else None
        if link is not None:
            if link.user_id != txn.user_id or link.invoice_id != txn.invoice_id:
                raise Conflict("This payment link does not match its order")
            if link.status == "cancelled":
                raise Conflict("This payment link was cancelled")
            exp = aware(link.expires_at)
            if exp and exp < datetime.now(timezone.utc):
                link.status = "expired"
                raise Conflict("This payment link has expired")
        if check_invoice and txn.invoice_id:
            inv = self.db.get(Invoice, txn.invoice_id, with_for_update={"of": Invoice})  # lock order: transaction, then invoice
            problem = invoice_problem(inv, txn) if inv is not None else None
            if problem:
                void_order(self.db, txn, f"Payment refused: {problem}")
                audit(self.db, txn.user_id, "payment.refused", "transaction", txn.id, {"reason": problem})
                self.db.commit()  # keep the voided state; the exception below would otherwise roll it back
                raise Conflict(f"This payment link is no longer valid: {problem}.")

    def reconcile_paypal_order(self, order_id: str, user_id=None) -> tuple[Transaction, dict]:
        """GET order from PayPal; if PayPal says it is captured but we have not recorded it, record it (verified)."""
        txn = self._txn_by_order("paypal", order_id, user_id, lock=True)
        order = self.paypal.get_order(order_id)
        if txn.status not in FINAL and order.get("status") == "COMPLETED":
            self.apply_capture(txn, parse_capture(order))
        return txn, order

    def mark_cancelled(self, provider: str, order_id: str) -> Transaction:
        txn = self._txn_by_order(provider, order_id, lock=True)
        if txn.status == "created":
            txn.status = "cancelled"
            add_event(txn, "cancelled", "Customer cancelled at checkout")
            self._close_link(txn, "cancelled")
        return txn

    def cancel_payment_link(self, link_id, user_id) -> PaymentLink:
        """Cancel a link AND the order behind it, so the old link can never be paid. Locks the order(s) first (same order as a
        capture), then re-reads the link: if a capture won the race the link is already 'paid' and this is refused."""
        link = PaymentLinkRepo(self.db, user_id).get(link_id)
        txns = self.db.scalars(select(Transaction).where(Transaction.payment_link_id == link.id).with_for_update(of=Transaction)).unique().all()
        self.db.refresh(link)
        if link.status != "active":
            raise AppError(f"Link is already {link.status}", code="invalid_state", status_code=409)
        for t in txns:
            if t.status in OPEN_STATUSES:
                void_order(self.db, t, "Payment link cancelled by the merchant")
        link.status = "cancelled"
        inv = self.db.get(Invoice, link.invoice_id) if link.invoice_id else None
        if inv is not None and inv.payment_link == link.url:
            inv.payment_link = inv.payment_provider = None
        audit(self.db, user_id, "payment_link.cancel", "payment_link", link.id)
        return link

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
        if inv is not None:
            reused = self._reuse_live_order(inv, "razorpay")
            if reused is not None:
                return reused
        txn_id = uuid.uuid4()
        order = self.razorpay.create_order(amount, currency, receipt=inv.invoice_number if inv else str(txn_id), notes={"transaction_id": str(txn_id)})
        description = description or (f"Invoice {inv.invoice_number}" if inv else "PayPilot payment")
        txn = Transaction(id=txn_id, user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None,
                          provider="razorpay", provider_order_id=order["id"], amount=amount, currency=currency, status="created", events=[],
                          invoice_version=inv.version if inv else None)
        add_event(txn, "order_created", f"Razorpay order {order['id']}")
        link_id = uuid.uuid4()
        link = PaymentLink(id=link_id, user_id=as_uuid(user_id), customer_id=customer_id, invoice_id=inv.id if inv else None,
                           provider="razorpay", provider_reference=order["id"],
                           url=f"{self.settings.frontend_url.rstrip('/')}/pay/razorpay/{link_id}", amount=amount, currency=currency,
                           description=description, status="active", expires_at=expires_at, invoice_version=inv.version if inv else None)
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
