from datetime import datetime, timezone

from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models import Customer, Invoice, Transaction
from app.repositories import TransactionRepo

SORTS = {"created_at": Transaction.created_at, "amount": Transaction.amount, "status": Transaction.status}


def add_event(txn: Transaction, kind: str, detail: str | None = None) -> None:
    """Append to the lifecycle timeline (reassign so SQLAlchemy sees the JSON mutation)."""
    txn.events = [*(txn.events or []), {"type": kind, "at": datetime.now(timezone.utc).isoformat(), "detail": detail}]


class TransactionService:
    def __init__(self, db: Session, user_id):
        self.db, self.user_id = db, user_id
        self.repo = TransactionRepo(db, user_id)

    def list(self, *, search=None, status=None, provider=None, sort="created_at", order="desc", page=1, page_size=20):
        q = self.repo.query()
        if status:
            q = q.where(Transaction.status == status)
        if provider:
            q = q.where(Transaction.provider == provider)
        if search:
            like = f"%{search.lower()}%"
            q = q.outerjoin(Customer, Transaction.customer_id == Customer.id).outerjoin(Invoice, Transaction.invoice_id == Invoice.id).where(
                or_(func.lower(func.coalesce(Customer.name, "")).like(like), func.lower(func.coalesce(Customer.email, "")).like(like),
                    func.lower(func.coalesce(Transaction.provider_order_id, "")).like(like),
                    func.lower(func.coalesce(Invoice.invoice_number, "")).like(like)))
        total = self.db.scalar(select(func.count()).select_from(q.with_only_columns(Transaction.id).subquery())) or 0
        col = SORTS.get(sort, Transaction.created_at)
        items = self.db.scalars(q.order_by(desc(col) if order == "desc" else asc(col)).offset((page - 1) * page_size).limit(page_size)).unique().all()
        return items, total

    def get(self, id_) -> Transaction:
        return self.repo.get(id_)
