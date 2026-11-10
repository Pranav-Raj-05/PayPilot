"""customers, invoices, transactions, payment links."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_payment_service
from app.core.db import get_db
from app.core.errors import AppError
from app.core.ratelimit import rate_limit
from app.core.security import CurrentUser, get_current_user
from app.models import PaymentLink
from app.repositories import PaymentLinkRepo
from app.schemas.common import Page
from app.schemas.entities import (CustomerIn, CustomerOut, CustomerUpdate, InvoiceIn, InvoiceOut, InvoiceUpdate, PaymentLinkIn, PaymentLinkOut, TransactionOut)
from app.services.common import audit
from app.services.customers import CustomerService
from app.services.invoices import InvoiceService
from app.services.payments import PaymentService
from app.services.transactions import TransactionService

customers_router = APIRouter(prefix="/api/customers", tags=["customers"])
invoices_router = APIRouter(prefix="/api/invoices", tags=["invoices"])
transactions_router = APIRouter(prefix="/api/transactions", tags=["transactions"])
links_router = APIRouter(prefix="/api/payment-links", tags=["payment-links"])

PageNo = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


# ---------------------------------------------------------------- customers
@customers_router.get("", response_model=Page[CustomerOut])
def list_customers(search: str | None = None, page: PageNo = 1, page_size: PageSize = 50, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    items, total = CustomerService(db, user.id).list(search, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@customers_router.post("", response_model=CustomerOut, status_code=201)
def create_customer(body: CustomerIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return CustomerService(db, user.id).create(body)


@customers_router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(customer_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return CustomerService(db, user.id).repo.get(customer_id)


@customers_router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: str, body: CustomerUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return CustomerService(db, user.id).update(customer_id, body)


@customers_router.delete("/{customer_id}", status_code=204)
def delete_customer(customer_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    CustomerService(db, user.id).delete(customer_id)


# ---------------------------------------------------------------- invoices
@invoices_router.get("", response_model=Page[InvoiceOut])
def list_invoices(status: str | None = None, search: str | None = None, page: PageNo = 1, page_size: PageSize = 50,
                  user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    items, total = InvoiceService(db, user.id).list(status, search, page, page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@invoices_router.post("", response_model=InvoiceOut, status_code=201)
def create_invoice(body: InvoiceIn, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return InvoiceService(db, user.id).create(body)


@invoices_router.get("/{invoice_id}", response_model=InvoiceOut)
def get_invoice(invoice_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return InvoiceService(db, user.id).get(invoice_id)


@invoices_router.patch("/{invoice_id}", response_model=InvoiceOut)
def update_invoice(invoice_id: str, body: InvoiceUpdate, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return InvoiceService(db, user.id).update(invoice_id, body)


@invoices_router.delete("/{invoice_id}", status_code=204)
def delete_invoice(invoice_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    InvoiceService(db, user.id).delete(invoice_id)


@invoices_router.post("/{invoice_id}/payment-link", response_model=PaymentLinkOut, status_code=201, dependencies=[Depends(rate_limit("pay-create", 20))])
def create_invoice_payment_link(invoice_id: str, provider: Literal["paypal", "razorpay"] = "paypal", user: CurrentUser = Depends(get_current_user),
                                payments: PaymentService = Depends(get_payment_service)):
    make = payments.create_paypal_order if provider == "paypal" else payments.create_razorpay_order
    _, link = make(user.id, invoice_id=invoice_id)
    return link


# ---------------------------------------------------------------- transactions
@transactions_router.get("", response_model=Page[TransactionOut])
def list_transactions(search: str | None = None, status: str | None = None, provider: str | None = None,
                      sort: Literal["created_at", "amount", "status"] = "created_at", order: Literal["asc", "desc"] = "desc",
                      page: PageNo = 1, page_size: PageSize = 20, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    items, total = TransactionService(db, user.id).list(search=search, status=status, provider=provider, sort=sort, order=order, page=page, page_size=page_size)
    return Page(items=items, total=total, page=page, page_size=page_size)


@transactions_router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return TransactionService(db, user.id).get(transaction_id)


# ---------------------------------------------------------------- payment links
@links_router.get("", response_model=list[PaymentLinkOut])
def list_links(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    repo = PaymentLinkRepo(db, user.id)
    return db.scalars(repo.query().order_by(PaymentLink.created_at.desc()).limit(200)).unique().all()


@links_router.post("", response_model=PaymentLinkOut, status_code=201, dependencies=[Depends(rate_limit("pay-create", 20))])
def create_link(body: PaymentLinkIn, user: CurrentUser = Depends(get_current_user), payments: PaymentService = Depends(get_payment_service)):
    make = payments.create_paypal_order if body.provider == "paypal" else payments.create_razorpay_order
    _, link = make(user.id, amount=body.amount, currency=body.currency, description=body.description, customer_id=body.customer_id, expires_at=body.expires_at)
    return link


@links_router.post("/{link_id}/cancel", response_model=PaymentLinkOut)
def cancel_link(link_id: str, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    link = PaymentLinkRepo(db, user.id).get(link_id)
    if link.status != "active":
        raise AppError(f"Link is already {link.status}", code="invalid_state", status_code=409)
    link.status = "cancelled"
    audit(db, user.id, "payment_link.cancel", "payment_link", link.id)
    return link
