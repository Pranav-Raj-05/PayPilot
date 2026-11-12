"""PayPilot Copilot tools. The model never touches the DB or payment credentials: it asks for a tool by name,
this module validates the typed input, enforces ownership (everything goes through user-scoped services) and returns
structured output. Sensitive tools only PREPARE an operation; execution needs explicit user approval."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, NotFound, ProviderError
from app.models import Customer, Invoice, Transaction
from app.repositories import InvoiceRepo, TransactionRepo
from app.schemas.entities import CustomerIn, InvoiceIn, InvoiceItemIn, InvoiceOut, InvoiceUpdate, TransactionOut
from app.services.analytics import AnalyticsService
from app.services.common import aware, as_uuid, audit, notify
from app.services.customers import CustomerService
from app.services.invoices import InvoiceService, compute_totals
from app.services.payments import PaymentService


@dataclass
class ToolContext:
    db: Session
    user_id: str
    settings: Settings
    payments: PaymentService
    default_currency: str = "USD"


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], dict]
    sensitive: bool = False
    prepare: Callable[[ToolContext, BaseModel], dict] | None = None


REGISTRY: dict[str, ToolSpec] = {}


def tool(name: str, description: str, model: type[BaseModel], *, sensitive=False, prepare=None):
    def deco(fn):
        REGISTRY[name] = ToolSpec(name, description, model, fn, sensitive, prepare)
        return fn
    return deco


def j(model_cls, obj) -> dict:
    return model_cls.model_validate(obj).model_dump(mode="json")


def find_invoice(ctx: ToolContext, ref: str) -> Invoice:
    repo = InvoiceRepo(ctx.db, ctx.user_id)
    try:
        return repo.get(uuid.UUID(ref))
    except ValueError:
        pass
    inv = ctx.db.scalars(repo.query().where(Invoice.invoice_number == ref.strip().upper())).unique().first()
    if not inv:
        raise NotFound(f"Invoice '{ref}' not found")
    return inv


def resolve_period(period: str, now: datetime) -> tuple[datetime, datetime, str]:
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now + timedelta(seconds=1)
    return {
        "today": (sod, end, "today"),
        "this_week": (sod - timedelta(days=sod.weekday()), end, "this week (since Monday)"),
        "this_month": (sod.replace(day=1), end, "this month"),
        "last_7_days": (now - timedelta(days=7), end, "the last 7 days"),
        "last_30_days": (now - timedelta(days=30), end, "the last 30 days"),
        "last_90_days": (now - timedelta(days=90), end, "the last 90 days"),
    }[period]


Period = Literal["today", "this_week", "this_month", "last_7_days", "last_30_days", "last_90_days"]


# =========================================================== create_invoice (sensitive)
class CreateInvoiceInput(BaseModel):
    customer_name: str = Field(description="Customer / client name")
    customer_email: str | None = Field(None, description="Customer email if given")
    amount: Decimal = Field(gt=0, description="Total amount for a single line item")
    currency: str = Field("USD", description="ISO currency code, e.g. USD, INR, EUR")
    description: str = Field(description="What the invoice is for, e.g. website development")
    due_in_days: int = Field(14, ge=0, le=365)
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, description="Tax percent")
    provider: Literal["paypal", "razorpay"] = Field("paypal", description="Payment provider; paypal unless the user asks for Razorpay/INR UPI")
    create_payment_link: bool = True


def _prepare_invoice(ctx: ToolContext, a: CreateInvoiceInput) -> dict:
    items = [InvoiceItemIn(description=a.description, quantity=Decimal(1), unit_price=a.amount)]
    InvoiceIn(items=items, currency=a.currency, tax_rate=a.tax_rate)  # validates currency
    if a.provider == "razorpay" and a.currency.upper() != "INR":
        raise AppError("Razorpay is only available for INR invoices; use PayPal for other currencies.", code="unsupported_currency")
    subtotal, tax, total = compute_totals(items, a.tax_rate)
    matches = CustomerService(ctx.db, ctx.user_id).find_by_name(a.customer_name)
    exact = next((c for c in matches if c.name.lower() == a.customer_name.strip().lower()), None) or (matches[0] if len(matches) == 1 else None)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    dup = None
    if exact:
        dup = ctx.db.scalars(select(Invoice).where(Invoice.user_id == as_uuid(ctx.user_id), Invoice.customer_id == exact.id,
                                                     Invoice.total_amount == total, Invoice.created_at >= since)).first()
    due = date.today() + timedelta(days=a.due_in_days)
    return {
        "title": f"Create {'PayPal' if a.provider == 'paypal' else 'Razorpay'} Invoice", "provider": a.provider,
        "mode": ctx.settings.paypal_mode if a.provider == "paypal" else "test",
        "customer": {"name": exact.name if exact else a.customer_name, "email": (exact.email if exact else None) or a.customer_email,
                     "existing": exact is not None, "id": str(exact.id) if exact else None},
        "terms": f"Net {a.due_in_days} Days" if a.due_in_days else "Due on receipt", "due_date": due.isoformat(), "currency": a.currency.upper(),
        "items": [{"description": a.description, "quantity": 1, "unit_price": float(a.amount), "amount": float(a.amount)}],
        "subtotal": float(subtotal), "tax": float(tax), "tax_rate": float(a.tax_rate), "total": float(total),
        "create_payment_link": a.create_payment_link,
        "checks": [
            {"ok": exact is not None, "text": "Customer profile found" if exact else "New customer will be created"},
            {"ok": dup is None, "text": "No duplicate invoice in the last 30 days" if dup is None else f"Possible duplicate: {dup.invoice_number} has the same amount"},
        ],
    }


@tool("create_invoice", "Prepare an invoice (and a payment link) for a customer. This only PREPARES the operation; the user must approve it in the UI before anything is created. "
      "Call it once you know customer name, amount and what it is for.", CreateInvoiceInput, sensitive=True, prepare=_prepare_invoice)
def create_invoice(ctx: ToolContext, a: CreateInvoiceInput) -> dict:
    customer = CustomerService(ctx.db, ctx.user_id).find_or_create(a.customer_name, a.customer_email)
    svc = InvoiceService(ctx.db, ctx.user_id)
    inv = svc.create(InvoiceIn(customer_id=customer.id, description=a.description, currency=a.currency, tax_rate=a.tax_rate,
                               due_date=date.today() + timedelta(days=a.due_in_days),
                               items=[InvoiceItemIn(description=a.description, quantity=Decimal(1), unit_price=a.amount)]))
    result: dict = {"invoice": j(InvoiceOut, inv), "payment_link": None, "warning": None}
    if a.create_payment_link:
        try:
            make = ctx.payments.create_paypal_order if a.provider == "paypal" else ctx.payments.create_razorpay_order
            txn, link = make(ctx.user_id, invoice_id=inv.id)
            result["payment_link"] = {"url": link.url, "provider": link.provider, "reference": link.provider_reference, "id": str(link.id)}
            result["transaction_id"] = str(txn.id)
            result["idempotency_key"] = str(txn.id)
            result["invoice"] = j(InvoiceOut, inv)
        except (ProviderError, AppError) as exc:
            result["warning"] = f"Invoice was created but the payment link failed: {exc.message}. Invoice remains a draft."
    return result


# =========================================================== get/update invoice
class InvoiceRefInput(BaseModel):
    invoice_ref: str = Field(description="Invoice number like INV-2026-0001 or the invoice id")


@tool("get_invoice", "Look up one invoice by number or id.", InvoiceRefInput)
def get_invoice(ctx: ToolContext, a: InvoiceRefInput) -> dict:
    InvoiceService(ctx.db, ctx.user_id).mark_overdue()
    return {"invoice": j(InvoiceOut, find_invoice(ctx, a.invoice_ref))}


class UpdateInvoiceInput(BaseModel):
    invoice_ref: str
    description: str | None = None
    due_date: date | None = None
    status: Literal["draft", "pending", "cancelled"] | None = None


def _prepare_update(ctx: ToolContext, a: UpdateInvoiceInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    changes = a.model_dump(exclude={"invoice_ref"}, exclude_none=True, mode="json")
    if not changes:
        raise AppError("No changes were specified", code="no_changes")
    return {"title": f"Update {inv.invoice_number}", "invoice_number": inv.invoice_number, "current_status": inv.status, "changes": changes}


@tool("update_invoice", "Prepare an update to an invoice (description, due date, cancel). Needs user approval.", UpdateInvoiceInput, sensitive=True, prepare=_prepare_update)
def update_invoice(ctx: ToolContext, a: UpdateInvoiceInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    inv = InvoiceService(ctx.db, ctx.user_id).update(inv.id, InvoiceUpdate(**a.model_dump(exclude={"invoice_ref"}, exclude_none=True)))
    return {"invoice": j(InvoiceOut, inv)}


# =========================================================== customers
class ListCustomersInput(BaseModel):
    search: str | None = Field(None, description="Name or email fragment")


@tool("list_customers", "List or search the user's customers.", ListCustomersInput)
def list_customers(ctx: ToolContext, a: ListCustomersInput) -> dict:
    items, total = CustomerService(ctx.db, ctx.user_id).list(a.search, 1, 25)
    return {"total": total, "customers": [{"id": str(c.id), "name": c.name, "email": c.email, "country": c.country} for c in items]}


class CreateCustomerInput(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    country: str | None = Field(None, description="2-letter ISO country code")


def _prepare_customer(ctx: ToolContext, a: CreateCustomerInput) -> dict:
    CustomerIn(**a.model_dump())
    return {"title": "Create customer", "name": a.name, "email": a.email, "phone": a.phone, "country": a.country}


@tool("create_customer", "Prepare a new customer record. Needs user approval.", CreateCustomerInput, sensitive=True, prepare=_prepare_customer)
def create_customer(ctx: ToolContext, a: CreateCustomerInput) -> dict:
    c = CustomerService(ctx.db, ctx.user_id).create(CustomerIn(**a.model_dump()))
    return {"customer": {"id": str(c.id), "name": c.name, "email": c.email}}


# =========================================================== payment links / PayPal orders (sensitive)
class PaymentLinkInput(BaseModel):
    amount: Decimal = Field(gt=0)
    currency: str = "USD"
    description: str
    provider: Literal["paypal", "razorpay"] = "paypal"
    customer_name: str | None = None
    invoice_ref: str | None = Field(None, description="If set, the link is for this invoice and its amount/currency are used")


def _prepare_link(ctx: ToolContext, a: PaymentLinkInput) -> dict:
    cur = a.currency.upper()
    if a.provider == "razorpay" and cur != "INR":
        raise AppError("Razorpay links must be in INR; use PayPal for other currencies.", code="unsupported_currency")
    InvoiceIn(items=[InvoiceItemIn(description="x", unit_price=a.amount)], currency=cur)
    base = {"title": f"Generate {a.provider.capitalize()} payment link", "provider": a.provider, "description": a.description}
    if a.invoice_ref:
        inv = find_invoice(ctx, a.invoice_ref)
        return {**base, "invoice_number": inv.invoice_number, "amount": float(inv.total_amount), "currency": inv.currency}
    return {**base, "amount": float(a.amount), "currency": cur, "customer_name": a.customer_name}


@tool("generate_payment_link", "Prepare a payment link (PayPal by default) for an amount or an existing invoice. Needs user approval.",
      PaymentLinkInput, sensitive=True, prepare=_prepare_link)
def generate_payment_link(ctx: ToolContext, a: PaymentLinkInput) -> dict:
    customer_id = None
    if a.customer_name:
        customer_id = CustomerService(ctx.db, ctx.user_id).find_or_create(a.customer_name).id
    inv_id = find_invoice(ctx, a.invoice_ref).id if a.invoice_ref else None
    make = ctx.payments.create_paypal_order if a.provider == "paypal" else ctx.payments.create_razorpay_order
    txn, link = make(ctx.user_id, invoice_id=inv_id, amount=a.amount, currency=a.currency.upper(), description=a.description, customer_id=customer_id)
    return {"payment_link": {"url": link.url, "provider": link.provider, "reference": link.provider_reference, "id": str(link.id),
                             "amount": float(link.amount), "currency": link.currency}, "transaction_id": str(txn.id), "idempotency_key": str(txn.id)}


class PayPalOrderInput(BaseModel):
    invoice_ref: str


def _prepare_order(ctx: ToolContext, a: PayPalOrderInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    if inv.status in ("paid", "cancelled"):
        raise AppError(f"Invoice {inv.invoice_number} is {inv.status}", code="invalid_state")
    return {"title": "Create PayPal order", "provider": "paypal", "invoice_number": inv.invoice_number, "amount": float(inv.total_amount), "currency": inv.currency}


@tool("create_paypal_order", "Prepare a PayPal order for an existing invoice. Needs user approval.", PayPalOrderInput, sensitive=True, prepare=_prepare_order)
def create_paypal_order(ctx: ToolContext, a: PayPalOrderInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    txn, link = ctx.payments.create_paypal_order(ctx.user_id, invoice_id=inv.id)
    return {"payment_link": {"url": link.url, "provider": "paypal", "reference": txn.provider_order_id, "id": str(link.id)}, "transaction_id": str(txn.id)}


# =========================================================== read-only analytics tools
class TransactionRefInput(BaseModel):
    reference: str = Field(description="Transaction id or provider order id")


@tool("get_transaction", "Look up one transaction by id or provider order id.", TransactionRefInput)
def get_transaction(ctx: ToolContext, a: TransactionRefInput) -> dict:
    repo = TransactionRepo(ctx.db, ctx.user_id)
    try:
        t = repo.get(uuid.UUID(a.reference))
    except ValueError:
        t = ctx.db.scalars(repo.query().where(Transaction.provider_order_id == a.reference)).unique().first()
        if not t:
            raise NotFound("Transaction not found")
    return {"transaction": j(TransactionOut, t)}


class ListTransactionsInput(BaseModel):
    status: Literal["created", "pending", "completed", "partially_refunded", "refunded", "failed", "cancelled"] | None = None
    period: Period = "last_30_days"
    limit: int = Field(10, ge=1, le=25)


@tool("list_transactions", "List transactions, optionally by status (e.g. failed payments) and period.", ListTransactionsInput)
def list_transactions(ctx: ToolContext, a: ListTransactionsInput) -> dict:
    start, end, label = resolve_period(a.period, datetime.now(timezone.utc))
    rows = [t for t in AnalyticsService(ctx.db, ctx.user_id)._txns(start, end) if not a.status or t.status == a.status]
    rows.sort(key=lambda t: aware(t.created_at), reverse=True)
    return {"period": label, "total": len(rows), "transactions": [
        {"id": str(t.id), "customer": t.customer.name if t.customer else None, "amount": float(t.amount), "currency": t.currency, "status": t.status,
         "provider": t.provider, "created_at": t.created_at.isoformat(), "failure_reason": t.failure_reason} for t in rows[: a.limit]]}


class RevenueInput(BaseModel):
    period: Period = "this_month"
    currency: str | None = Field(None, description="Defaults to the user's main currency")


@tool("get_revenue_summary", "Revenue (completed payments only) for a period: total, count, average, by provider.", RevenueInput)
def get_revenue_summary(ctx: ToolContext, a: RevenueInput) -> dict:
    svc = AnalyticsService(ctx.db, ctx.user_id)
    cur = (a.currency or (None if svc.currencies() else ctx.default_currency))
    cur = svc.pick_currency(cur)
    start, end, label = resolve_period(a.period, svc.now)
    done = [t for t in svc._txns(start, end, cur) if t.status in ("completed", "partially_refunded")]
    net = lambda t: float(t.amount) - float(t.refunded_amount or 0)  # what the merchant kept after partial refunds
    total = sum(net(t) for t in done)
    by_provider: dict[str, float] = {}
    for t in done:
        by_provider[t.provider] = round(by_provider.get(t.provider, 0) + net(t), 2)
    return {"period": label, "currency": cur, "revenue": round(total, 2), "payments": len(done),
            "average_payment": round(total / len(done), 2) if done else 0, "by_provider": by_provider}


class NoInput(BaseModel):
    pass


@tool("get_pending_payments", "Open (pending/overdue) invoices with customers and aging. Use for 'pending payments' and 'overdue invoices'.", NoInput)
def get_pending_payments(ctx: ToolContext, a: NoInput) -> dict:
    InvoiceService(ctx.db, ctx.user_id).mark_overdue()
    rows = ctx.db.scalars(select(Invoice).where(Invoice.user_id == as_uuid(ctx.user_id), Invoice.status.in_(["pending", "overdue"])).order_by(Invoice.due_date)).unique().all()
    out = [{"invoice_number": i.invoice_number, "customer": i.customer.name if i.customer else None, "amount": float(i.total_amount),
            "currency": i.currency, "status": i.status, "due_date": i.due_date.isoformat() if i.due_date else None,
            "days_overdue": max(0, (date.today() - i.due_date).days) if i.due_date else 0} for i in rows]
    return {"count": len(out), "overdue_count": sum(1 for r in out if r["status"] == "overdue"), "invoices": out}


class AnalyseInput(BaseModel):
    days: int = Field(30, ge=1, le=365)


@tool("analyse_business_performance", "Revenue trend, success rate, top customers and insights for the last N days.", AnalyseInput)
def analyse_business_performance(ctx: ToolContext, a: AnalyseInput) -> dict:
    data = AnalyticsService(ctx.db, ctx.user_id).analytics(a.days)
    return {k: data[k] for k in ("currency", "days", "success_rate", "average_transaction_value", "top_customers", "revenue_by_provider", "insights")} | {
        "revenue": {k: v for k, v in data["revenue"].items() if k not in ("series", "previous_series")}, "transactions": data["volume"]["total"]}


class ReminderInput(BaseModel):
    invoice_ref: str | None = Field(None, description="Specific invoice, or omit for all overdue invoices")


@tool("suggest_payment_reminder", "Draft polite payment reminder messages for overdue invoices. Drafts only; nothing is sent.", ReminderInput)
def suggest_payment_reminder(ctx: ToolContext, a: ReminderInput) -> dict:
    InvoiceService(ctx.db, ctx.user_id).mark_overdue()
    if a.invoice_ref:
        invs = [find_invoice(ctx, a.invoice_ref)]
    else:
        invs = list(ctx.db.scalars(select(Invoice).where(Invoice.user_id == as_uuid(ctx.user_id), Invoice.status == "overdue")).unique().all())
    drafts = []
    for i in invs[:10]:
        who = i.customer.name if i.customer else "there"
        days = (date.today() - i.due_date).days if i.due_date else 0
        drafts.append({"invoice_number": i.invoice_number, "customer": who, "email": i.customer.email if i.customer else None, "days_overdue": days,
                       "message": f"Hi {who}, a friendly reminder that invoice {i.invoice_number} for {i.currency} {i.total_amount} was due {days} day(s) ago. "
                                  f"You can pay securely here: {i.payment_link or '(payment link pending)'}. Thank you!"})
    return {"drafts": drafts, "note": "Drafts only. Ask me to queue a reminder to record it after you approve."}


class QueueReminderInput(BaseModel):
    invoice_ref: str
    message: str


def _prepare_reminder(ctx: ToolContext, a: QueueReminderInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    return {"title": f"Queue reminder for {inv.invoice_number}", "invoice_number": inv.invoice_number, "message": a.message,
            "note": "Saved in-app. Email/SMS delivery is not configured in this deployment."}


@tool("queue_payment_reminder", "Prepare recording a payment reminder for an invoice. Needs user approval; does NOT send email.", QueueReminderInput, sensitive=True, prepare=_prepare_reminder)
def queue_payment_reminder(ctx: ToolContext, a: QueueReminderInput) -> dict:
    inv = find_invoice(ctx, a.invoice_ref)
    notify(ctx.db, ctx.user_id, f"Reminder recorded: {inv.invoice_number}", a.message, "reminder", {"invoice_id": str(inv.id)})
    audit(ctx.db, ctx.user_id, "reminder.queued", "invoice", inv.id)
    return {"delivered": False, "recorded": True, "note": "Reminder recorded in-app; no email was sent."}


def tool_declarations() -> list[dict]:
    from app.ai.providers import clean_schema
    return [{"name": s.name, "description": s.description, "parameters": clean_schema(s.input_model.model_json_schema())} for s in REGISTRY.values()]
