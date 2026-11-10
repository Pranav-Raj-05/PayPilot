"""Razorpay (secondary gateway). Entirely separate from PayPal: own credentials, own verification."""
import hashlib
import hmac
import logging
from decimal import Decimal

import httpx

from app.core.config import Settings
from app.core.errors import NotConfigured, ProviderError

log = logging.getLogger("paypilot.razorpay")
API = "https://api.razorpay.com/v1"


def verify_payment_signature(order_id: str, payment_id: str, signature: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not secret:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


class RazorpayClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=20)

    def _require(self):
        if not self.settings.razorpay_configured:
            raise NotConfigured("Razorpay credentials are not configured (RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)")

    def _call(self, method: str, path: str, json=None) -> dict:
        self._require()
        try:
            r = self.http.request(method, API + path, json=json, auth=(self.settings.razorpay_key_id, self.settings.razorpay_key_secret))
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach Razorpay") from exc
        if r.status_code not in (200, 201):
            log.warning("razorpay %s %s -> %s %s", method, path, r.status_code, r.text[:300])
            raise ProviderError("Razorpay request failed", details={"status": r.status_code})
        return r.json()

    def create_order(self, amount: Decimal, currency: str, receipt: str, notes: dict) -> dict:
        paise = int((Decimal(amount) * 100).to_integral_value())
        return self._call("POST", "/orders", {"amount": paise, "currency": currency, "receipt": receipt[:40], "notes": notes})

    def get_payment(self, payment_id: str) -> dict:
        return self._call("GET", f"/payments/{payment_id}")
