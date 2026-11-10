from fastapi import Depends
from sqlalchemy.orm import Session

from app.ai.providers import LLMProvider, get_provider
from app.ai.tools import ToolContext
from app.core.config import Settings, get_settings
from app.core.db import get_db
from app.core.security import CurrentUser, get_current_user
from app.models import Profile
from app.payments.paypal.client import PayPalClient
from app.payments.razorpay.client import RazorpayClient
from app.services.common import as_uuid
from app.services.payments import PaymentService

_paypal: PayPalClient | None = None
_razorpay: RazorpayClient | None = None


def get_paypal(settings: Settings = Depends(get_settings)) -> PayPalClient:
    global _paypal
    if _paypal is None:
        _paypal = PayPalClient(settings)
    return _paypal


def get_razorpay(settings: Settings = Depends(get_settings)) -> RazorpayClient:
    global _razorpay
    if _razorpay is None:
        _razorpay = RazorpayClient(settings)
    return _razorpay


def get_payment_service(db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                        paypal: PayPalClient = Depends(get_paypal), razorpay: RazorpayClient = Depends(get_razorpay)) -> PaymentService:
    return PaymentService(db, settings, paypal, razorpay)


def get_llm(settings: Settings = Depends(get_settings)) -> LLMProvider:
    return get_provider(settings)


def get_tool_context(user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
                     payments: PaymentService = Depends(get_payment_service)) -> ToolContext:
    profile = db.get(Profile, as_uuid(user.id))
    return ToolContext(db=db, user_id=user.id, settings=settings, payments=payments, default_currency=(profile.default_currency if profile else "USD"))
