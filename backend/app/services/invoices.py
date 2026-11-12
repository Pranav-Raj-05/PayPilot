from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError, Conflict
from app.models import Customer, Invoice, InvoiceItem
from app.repositories import CustomerRepo, InvoiceRepo
from app.schemas.entities import InvoiceIn, InvoiceItemIn, InvoiceUpdate
from app.services.common import audit
from app.services.orders import live_orders, void_order

CENT = Decimal("0.01")


def q2(v: Decimal) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def compute_totals(items: list[InvoiceItemIn], tax_rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    subtotal = q2(sum((q2(i.quantity * i.unit_price) for i in items), Decimal("0")))
    tax = q2(subtotal * Decimal(tax_rate) / Decimal(100))
    return subtotal, tax, q2(subtotal + tax)


class InvoiceService:
    def __init__(self, db: Session, user_id):
        self.db, self.user_id = db, user_id
        self.repo = InvoiceRepo(db, user_id)

    # ---- queries
    def mark_overdue(self) -> int:
        res = self.db.execute(
            update(Invoice)
            .where(Invoice.user_id == self.repo.user_id, Invoice.status == "pending", Invoice.due_date.is_not(None),
                   Invoice.due_date < date.today())
            .values(status="overdue")
        )
        return res.rowcount or 0

    def list(self, status: str | None = None, search: str | None = None, page: int = 1, page_size: int = 50):
        self.mark_overdue()
        q = self.repo.query()
        if status:
            q = q.where(Invoice.status == status)
        if search:
            like = f"%{search.lower()}%"
            q = q.outerjoin(Customer, Invoice.customer_id == Customer.id).where(
                or_(func.lower(Invoice.invoice_number).like(like), func.lower(func.coalesce(Customer.name, "")).like(like)))
        total = self.db.scalar(select(func.count()).select_from(q.with_only_columns(Invoice.id).subquery())) or 0
        items = self.db.scalars(q.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).unique().all()
        return items, total

    def get(self, id_) -> Invoice:
        return self.repo.get(id_)

    # ---- commands
    def _next_number(self) -> str:
        year = datetime.now(timezone.utc).year
        count = self.db.scalar(select(func.count()).select_from(Invoice).where(Invoice.user_id == self.repo.user_id)) or 0
        while True:
            count += 1
            number = f"INV-{year}-{count:04d}"
            if not self.db.scalar(select(Invoice.id).where(Invoice.user_id == self.repo.user_id, Invoice.invoice_number == number)):
                return number

    def _check_customer(self, customer_id):
        if customer_id is not None:
            CustomerRepo(self.db, self.user_id).get(customer_id)  # ownership validation

    def create(self, data: InvoiceIn, status: str = "draft") -> Invoice:
        self._check_customer(data.customer_id)
        subtotal, tax, total = compute_totals(data.items, data.tax_rate)
        if total <= 0:
            raise AppError("Invoice total must be greater than zero", code="invalid_amount")
        inv = Invoice(customer_id=data.customer_id, invoice_number=self._next_number(), description=data.description,
                      subtotal=subtotal, tax=tax, total_amount=total, currency=data.currency,
                      due_date=data.due_date, status=status)
        inv.items = [InvoiceItem(position=n, description=i.description, quantity=i.quantity, unit_price=q2(i.unit_price),
                                 total=q2(i.quantity * i.unit_price)) for n, i in enumerate(data.items)]
        self.repo.add(inv)
        audit(self.db, self.user_id, "invoice.create", "invoice", inv.id, {"total": str(total), "currency": inv.currency})
        return inv

    # Fields a payment depends on. Changing any of them bumps Invoice.version, which invalidates every order made for the old version.
    PAYMENT_FIELDS = ("customer_id", "items", "tax_rate", "currency")

    def update(self, id_, data: InvoiceUpdate) -> Invoice:
        inv = self.repo.get(id_)
        fields = data.model_dump(exclude_unset=True)
        content_edit = any(k in fields for k in ("customer_id", "description", "items", "tax_rate", "currency", "due_date"))
        if inv.status == "paid":
            raise Conflict("Paid invoices cannot be modified")
        # Lock the invoice's open orders FIRST (the order a capture uses), then re-read the invoice: if a capture won the race
        # the invoice is now paid and the change is refused; if this change wins, the capture is refused instead.
        live = live_orders(self.db, inv.id, lock=True)
        self.db.refresh(inv, with_for_update={"of": Invoice})
        if inv.status == "paid":
            raise Conflict("Paid invoices cannot be modified")
        if live and (content_edit or fields.get("status") == "draft"):
            raise Conflict("This invoice has an active payment link. Cancel the payment link before editing the invoice.", code="payment_link_active")
        if content_edit and inv.status not in ("draft",):
            raise Conflict("Only draft invoices can be edited")
        if fields.get("status") == "cancelled":
            for txn in live:  # cancelling the invoice cancels what a customer could still pay
                void_order(self.db, txn, "The invoice was cancelled")
            inv.payment_link = inv.payment_provider = None
        if any(k in fields for k in self.PAYMENT_FIELDS):
            inv.version = (inv.version or 1) + 1
        if "customer_id" in fields:
            self._check_customer(fields["customer_id"])
            inv.customer_id = fields["customer_id"]
        for k in ("description", "currency", "due_date"):
            if k in fields:
                setattr(inv, k, fields[k])
        if "items" in fields or "tax_rate" in fields:
            items = data.items or [InvoiceItemIn(description=i.description, quantity=i.quantity, unit_price=i.unit_price) for i in inv.items]
            rate = data.tax_rate if data.tax_rate is not None else (inv.tax / inv.subtotal * 100 if inv.subtotal else Decimal(0))
            inv.subtotal, inv.tax, inv.total_amount = compute_totals(items, rate)
            if data.items:
                inv.items = [InvoiceItem(position=n, description=i.description, quantity=i.quantity, unit_price=q2(i.unit_price),
                                         total=q2(i.quantity * i.unit_price)) for n, i in enumerate(data.items)]
        if fields.get("status"):
            inv.status = fields["status"]
        audit(self.db, self.user_id, "invoice.update", "invoice", inv.id, {"fields": sorted(fields)})
        self.db.flush()
        return inv

    def delete(self, id_) -> None:
        inv = self.repo.get(id_)
        if inv.status != "draft":
            raise Conflict("Only draft invoices can be deleted")
        if live_orders(self.db, inv.id, lock=True):
            raise Conflict("This invoice has an active payment link. Cancel it before deleting the invoice.", code="payment_link_active")
        self.db.delete(inv)
        audit(self.db, self.user_id, "invoice.delete", "invoice", id_)

    def default_due_date(self, days: int = 14) -> date:
        return date.today() + timedelta(days=days)
