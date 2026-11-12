"""Rules shared by invoices and payments about *open payment orders*: an order is live while a customer could still pay it.

Why this exists: an invoice must never be marked paid by a payment that was created for a different invoice state (amount,
currency, version) or for an invoice that has since been cancelled. Both InvoiceService (edits/cancels) and PaymentService
(captures) use these helpers so the two sides cannot drift apart.

Lock order everywhere: transaction rows first, then the invoice, then the payment link. Keeping one order avoids deadlocks
between a capture and a concurrent cancel/edit.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Invoice, PaymentLink, Transaction
from app.services.common import aware
from app.services.transactions import add_event

OPEN_STATUSES = ("created", "pending")  # an order the customer has not finished paying
PAYABLE_INVOICE_STATUSES = ("pending", "overdue")


def link_is_usable(link: PaymentLink | None, now: datetime | None = None) -> bool:
    if link is None:
        return True  # an order without a link row is still capturable through its order id
    if link.status != "active":
        return False
    exp = aware(link.expires_at)
    return not (exp and exp < (now or datetime.now(timezone.utc)))


def live_orders(db: Session, invoice_id, *, lock: bool = False) -> list[Transaction]:
    """Open orders for an invoice that a customer could still complete."""
    q = select(Transaction).where(Transaction.invoice_id == invoice_id, Transaction.status.in_(OPEN_STATUSES))
    if lock:
        q = q.with_for_update(of=Transaction)
    out = []
    for txn in db.scalars(q).unique().all():
        link = db.get(PaymentLink, txn.payment_link_id) if txn.payment_link_id else None
        if link_is_usable(link):
            out.append(txn)
    return out


def void_order(db: Session, txn: Transaction, reason: str) -> None:
    """Make an open order unpayable: the transaction and its link are cancelled together."""
    if txn.status in OPEN_STATUSES:
        txn.status = "cancelled"
        add_event(txn, "cancelled", reason)
    if txn.payment_link_id:
        link = db.get(PaymentLink, txn.payment_link_id)
        if link is not None and link.status == "active":
            link.status = "cancelled"


def invoice_problem(inv: Invoice, txn: Transaction) -> str | None:
    """Why `txn` may NOT mark `inv` as paid (None = it may). Checked before a capture and again when recording one."""
    if inv.user_id != txn.user_id:
        return "the invoice belongs to a different account"
    if inv.status == "cancelled":
        return "the invoice was cancelled"
    if inv.status == "paid":
        return "the invoice is already paid"
    if inv.status not in PAYABLE_INVOICE_STATUSES:
        return f"the invoice is {inv.status}"
    if txn.invoice_version is not None and txn.invoice_version != inv.version:
        return "the invoice changed after the payment link was created"
    if Decimal(inv.total_amount) != Decimal(txn.amount) or inv.currency != txn.currency:
        return "the invoice amount or currency changed after the payment link was created"
    return None
