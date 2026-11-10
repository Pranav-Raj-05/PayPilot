from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.models import Customer, Invoice
from app.repositories import CustomerRepo
from app.schemas.entities import CustomerIn, CustomerUpdate
from app.services.common import audit


class CustomerService:
    def __init__(self, db: Session, user_id):
        self.db, self.user_id = db, user_id
        self.repo = CustomerRepo(db, user_id)

    def list(self, search: str | None = None, page: int = 1, page_size: int = 50):
        q = self.repo.query()
        if search:
            like = f"%{search.lower()}%"
            q = q.where(or_(func.lower(Customer.name).like(like), func.lower(func.coalesce(Customer.email, "")).like(like)))
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        items = self.db.scalars(q.order_by(Customer.name).offset((page - 1) * page_size).limit(page_size)).all()
        return items, total

    def create(self, data: CustomerIn) -> Customer:
        c = Customer(**data.model_dump())
        self.repo.add(c)
        audit(self.db, self.user_id, "customer.create", "customer", c.id)
        return c

    def find_by_name(self, name: str) -> "list[Customer]":
        like = f"%{name.strip().lower()}%"
        return list(self.db.scalars(self.repo.query().where(func.lower(Customer.name).like(like)).limit(5)).all())

    def find_or_create(self, name: str, email: str | None = None) -> Customer:
        exact = self.db.scalars(self.repo.query().where(func.lower(Customer.name) == name.strip().lower())).first()
        if exact:
            return exact
        return self.create(CustomerIn(name=name.strip(), email=email))

    def update(self, id_, data: CustomerUpdate) -> Customer:
        c = self.repo.get(id_)
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(c, k, v)
        audit(self.db, self.user_id, "customer.update", "customer", c.id)
        return c

    def delete(self, id_) -> None:
        c = self.repo.get(id_)
        if self.db.scalar(select(func.count()).select_from(Invoice).where(Invoice.customer_id == c.id)):
            raise Conflict("Customer has invoices and cannot be deleted")
        self.db.delete(c)
        audit(self.db, self.user_id, "customer.delete", "customer", id_)
