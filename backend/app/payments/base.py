from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class CreatedOrder:
    order_id: str
    status: str
    approval_url: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class CaptureResult:
    """Provider-normalised capture outcome. status: completed | pending | failed"""
    status: str
    order_id: str
    capture_id: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    payment_method: str | None = None
    reason: str | None = None
    fee: Decimal | None = None
    net: Decimal | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    """Provider-normalised refund. status: completed | pending | failed. Every provider (PayPal now, Razorpay later) maps into this."""
    provider_refund_id: str
    status: str
    amount: Decimal | None = None
    currency: str | None = None
    raw: dict = field(default_factory=dict)
