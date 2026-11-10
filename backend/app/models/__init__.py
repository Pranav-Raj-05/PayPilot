import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


def _pk():
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def _updated():
    return mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


MONEY = Numeric(14, 2)


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # = auth.users.id
    email: Mapped[str] = mapped_column(String(320), default="")
    full_name: Mapped[str | None] = mapped_column(String(200))
    business_name: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    default_currency: Mapped[str] = mapped_column(String(3), default="USD")
    created_at: Mapped[datetime] = _created()


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_user", "user_id"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = _created()


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("user_id", "invoice_number", name="uq_invoice_number"),
        Index("ix_invoices_user_status", "user_id", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    invoice_number: Mapped[str] = mapped_column(String(40))
    description: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, default=0)
    tax: Mapped[Decimal] = mapped_column(MONEY, default=0)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|pending|paid|overdue|cancelled
    payment_provider: Mapped[str | None] = mapped_column(String(20))
    payment_link: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    customer: Mapped["Customer | None"] = relationship(lazy="joined")
    items: Mapped[list["InvoiceItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin", order_by="InvoiceItem.position")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[uuid.UUID] = _pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=1)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    total: Mapped[Decimal] = mapped_column(MONEY)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint("provider", "provider_order_id", name="uq_txn_provider_order"),
        Index("ix_txn_user_created", "user_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    payment_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payment_links.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(20))  # paypal | razorpay
    provider_order_id: Mapped[str | None] = mapped_column(String(100))
    provider_capture_id: Mapped[str | None] = mapped_column(String(100))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="created")  # created|pending|completed|failed|refunded|cancelled
    payment_method: Mapped[str | None] = mapped_column(String(50))
    fee_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    net_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    events: Mapped[list] = mapped_column(JSON, default=list)  # lifecycle timeline
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    customer: Mapped["Customer | None"] = relationship(lazy="joined")
    invoice: Mapped["Invoice | None"] = relationship(lazy="joined")

    @property
    def invoice_number(self) -> str | None:
        return self.invoice.invoice_number if self.invoice else None


class PaymentLink(Base):
    __tablename__ = "payment_links"
    __table_args__ = (Index("ix_links_user", "user_id"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"))
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invoices.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(20))
    provider_reference: Mapped[str | None] = mapped_column(String(100))
    url: Mapped[str] = mapped_column(Text)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|paid|expired|cancelled
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()

    customer: Mapped["Customer | None"] = relationship(lazy="joined")


class AIConversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AIMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ai_conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(80))
    tool_result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class Automation(Base):
    __tablename__ = "automations"
    __table_args__ = (UniqueConstraint("user_id", "automation_type", name="uq_automation_type"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    automation_type: Mapped[str] = mapped_column(String(50))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_user_read", "user_id", "is_read"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    notification_type: Mapped[str] = mapped_column(String(40), default="info")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class WebhookEvent(Base):
    """Dedupes provider webhook deliveries (providers retry)."""
    __tablename__ = "webhook_events"
    __table_args__ = (UniqueConstraint("provider", "event_id", name="uq_webhook_event"),)
    id: Mapped[uuid.UUID] = _pk()
    provider: Mapped[str] = mapped_column(String(20))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()
