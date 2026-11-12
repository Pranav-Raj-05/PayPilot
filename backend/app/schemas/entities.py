import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORM, Money

Currency = str
SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "INR", "AUD", "CAD", "SGD", "JPY"}


def _currency(v: str) -> str:
    v = v.upper()
    if v not in SUPPORTED_CURRENCIES:
        raise ValueError(f"Unsupported currency {v}. Supported: {', '.join(sorted(SUPPORTED_CURRENCIES))}")
    return v


# ---------- profile ----------
class ProfileOut(ORM):
    id: uuid.UUID
    email: str
    full_name: str | None
    business_name: str | None
    avatar_url: str | None
    default_currency: str
    created_at: datetime


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    business_name: str | None = Field(None, max_length=200)
    avatar_url: str | None = Field(None, max_length=1000)
    default_currency: str | None = None

    @field_validator("default_currency")
    @classmethod
    def _c(cls, v):
        return _currency(v) if v else v


# ---------- customers ----------
class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=500)
    country: str | None = Field(None, min_length=2, max_length=2)


class CustomerUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = None
    address: str | None = None
    country: str | None = Field(None, min_length=2, max_length=2)


class CustomerOut(ORM):
    id: uuid.UUID
    name: str
    email: str | None
    phone: str | None
    address: str | None
    country: str | None
    created_at: datetime


class CustomerRef(ORM):
    id: uuid.UUID
    name: str
    email: str | None


# ---------- invoices ----------
class InvoiceItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: Decimal = Field(default=Decimal("1"), gt=0, le=100000)
    unit_price: Decimal = Field(ge=0, le=Decimal("100000000"))


class InvoiceItemOut(ORM):
    id: uuid.UUID
    description: str
    quantity: Money
    unit_price: Money
    total: Money


InvoiceStatus = Literal["draft", "pending", "paid", "overdue", "cancelled"]


class InvoiceIn(BaseModel):
    customer_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=2000)
    items: list[InvoiceItemIn] = Field(min_length=1, max_length=50)
    tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=100, description="Percent")
    currency: str = "USD"
    due_date: date | None = None

    @field_validator("currency")
    @classmethod
    def _c(cls, v):
        return _currency(v)


class InvoiceUpdate(BaseModel):
    customer_id: uuid.UUID | None = None
    description: str | None = None
    items: list[InvoiceItemIn] | None = Field(None, min_length=1, max_length=50)
    tax_rate: Decimal | None = Field(None, ge=0, le=100)
    currency: str | None = None
    due_date: date | None = None
    status: Literal["draft", "pending", "cancelled"] | None = None

    @field_validator("currency")
    @classmethod
    def _c(cls, v):
        return _currency(v) if v else v


class InvoiceOut(ORM):
    id: uuid.UUID
    invoice_number: str
    customer_id: uuid.UUID | None
    customer: CustomerRef | None
    description: str | None
    subtotal: Money
    tax: Money
    total_amount: Money
    currency: str
    due_date: date | None
    status: str
    payment_provider: str | None
    payment_link: str | None
    version: int = 1
    items: list[InvoiceItemOut]
    created_at: datetime
    updated_at: datetime


# ---------- transactions ----------
class RefundOut(ORM):
    id: uuid.UUID
    provider_refund_id: str
    amount: Money
    currency: str
    status: str  # pending | completed | failed
    source: str
    reason: str | None = None
    created_at: datetime


class TransactionOut(ORM):
    id: uuid.UUID
    customer_id: uuid.UUID | None
    customer: CustomerRef | None
    invoice_id: uuid.UUID | None
    invoice_number: str | None = None
    provider: str
    provider_order_id: str | None
    provider_capture_id: str | None
    amount: Money
    currency: str
    status: str
    payment_method: str | None
    fee_amount: Money | None = None
    net_amount: Money | None = None
    failure_reason: str | None
    events: list
    refunded_amount: Money = Decimal(0)  # sum of COMPLETED refunds; `amount` stays the original payment
    refunds: list[RefundOut] = []
    created_at: datetime
    updated_at: datetime


# ---------- payment links ----------
class PaymentLinkIn(BaseModel):
    provider: Literal["paypal", "razorpay"] = "paypal"
    amount: Decimal = Field(gt=0, le=Decimal("100000000"))
    currency: str = "USD"
    description: str = Field(min_length=1, max_length=500)
    customer_id: uuid.UUID | None = None
    expires_at: datetime | None = None

    @field_validator("currency")
    @classmethod
    def _c(cls, v):
        return _currency(v)


class PaymentLinkOut(ORM):
    id: uuid.UUID
    customer_id: uuid.UUID | None
    customer: CustomerRef | None
    invoice_id: uuid.UUID | None
    provider: str
    provider_reference: str | None
    url: str
    amount: Money
    currency: str
    description: str | None
    status: str
    expires_at: datetime | None
    created_at: datetime


# ---------- misc ----------
class NotificationOut(ORM):
    id: uuid.UUID
    title: str
    message: str
    notification_type: str
    is_read: bool
    data: dict | None
    created_at: datetime


class AutomationOut(ORM):
    id: uuid.UUID
    name: str
    description: str | None
    automation_type: str
    is_enabled: bool
    configuration: dict
    last_executed_at: datetime | None
    created_at: datetime


class AutomationUpdate(BaseModel):
    is_enabled: bool | None = None
    configuration: dict | None = None
