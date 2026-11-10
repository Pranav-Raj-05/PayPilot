"""Thin PayPal REST client (Orders v2 + webhook verification). Credentials never leave the backend."""
import logging
import threading
import time
from decimal import Decimal

import httpx

from app.core.config import Settings
from app.core.errors import NotConfigured, ProviderError
from app.payments.base import CaptureResult, CreatedOrder

log = logging.getLogger("paypilot.paypal")

ZERO_DECIMAL = {"JPY"}


def fmt_amount(amount: Decimal, currency: str) -> str:
    return f"{Decimal(amount):.0f}" if currency in ZERO_DECIMAL else f"{Decimal(amount):.2f}"


class PayPalClient:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=20)
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    @property
    def base(self) -> str:
        return self.settings.paypal_base_url.rstrip("/")

    def _require(self) -> None:
        if not self.settings.paypal_configured:
            raise NotConfigured("PayPal credentials are not configured (PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET)")

    def access_token(self) -> str:
        self._require()
        with self._lock:
            if self._token and time.time() < self._expires - 30:
                return self._token
            try:
                r = self.http.post(f"{self.base}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                                   auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret))
            except httpx.HTTPError as exc:
                raise ProviderError("Could not reach PayPal") from exc
            if r.status_code != 200:
                log.error("paypal oauth failed status=%s", r.status_code)
                raise ProviderError("PayPal authentication failed - check sandbox credentials")
            body = r.json()
            self._token = body["access_token"]
            self._expires = time.time() + int(body.get("expires_in", 300))
            return self._token

    def _request(self, method: str, path: str, *, json=None, request_id: str | None = None, ok=(200, 201)) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # idempotency key
        try:
            r = self.http.request(method, f"{self.base}{path}", json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError("Could not reach PayPal") from exc
        if r.status_code not in ok:
            log.warning("paypal %s %s -> %s %s", method, path, r.status_code, r.text[:300])
        return r

    # ---- Orders v2
    def create_order(self, *, amount: Decimal, currency: str, reference_id: str, custom_id: str, description: str,
                     return_url: str, cancel_url: str, request_id: str) -> CreatedOrder:
        body = {
            "intent": "CAPTURE",
            "purchase_units": [{
                "reference_id": reference_id[:256], "custom_id": custom_id[:127], "description": description[:127],
                "amount": {"currency_code": currency, "value": fmt_amount(amount, currency)},
            }],
            "payment_source": {"paypal": {"experience_context": {
                "brand_name": "PayPilot", "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                "return_url": return_url, "cancel_url": cancel_url,
            }}},
        }
        r = self._request("POST", "/v2/checkout/orders", json=body, request_id=request_id)
        if r.status_code not in (200, 201):
            raise ProviderError("PayPal could not create the order", details=_err(r))
        data = r.json()
        links = {l["rel"]: l["href"] for l in data.get("links", [])}
        return CreatedOrder(order_id=data["id"], status=data["status"], approval_url=links.get("payer-action") or links.get("approve"), raw=data)

    def get_order(self, order_id: str) -> dict:
        r = self._request("GET", f"/v2/checkout/orders/{order_id}")
        if r.status_code == 404:
            raise ProviderError("PayPal order not found", status_code=404, code="provider_not_found")
        if r.status_code != 200:
            raise ProviderError("PayPal could not fetch the order", details=_err(r))
        return r.json()

    def capture_order(self, order_id: str, request_id: str) -> CaptureResult:
        r = self._request("POST", f"/v2/checkout/orders/{order_id}/capture", json={}, request_id=request_id)
        if r.status_code in (200, 201):
            return parse_capture(r.json())
        issue = (_err(r) or {}).get("issue")
        if r.status_code == 422 and issue == "ORDER_ALREADY_CAPTURED":
            return parse_capture(self.get_order(order_id))  # idempotent: read back the truth
        if r.status_code == 422 and issue in ("ORDER_NOT_APPROVED", "PAYER_ACTION_REQUIRED"):
            return CaptureResult(status="failed", order_id=order_id, reason="Customer has not approved the order yet")
        if r.status_code == 422 and issue in ("INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"):
            return CaptureResult(status="failed", order_id=order_id, reason=f"Payment declined ({issue})")
        raise ProviderError("PayPal capture failed", details=_err(r))

    # ---- Webhooks
    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        self._require()
        if not self.settings.paypal_webhook_id:
            raise NotConfigured("PAYPAL_WEBHOOK_ID is not configured")
        h = {k.lower(): v for k, v in headers.items()}
        try:
            body = {
                "auth_algo": h["paypal-auth-algo"], "cert_url": h["paypal-cert-url"],
                "transmission_id": h["paypal-transmission-id"], "transmission_sig": h["paypal-transmission-sig"],
                "transmission_time": h["paypal-transmission-time"], "webhook_id": self.settings.paypal_webhook_id,
                "webhook_event": event,
            }
        except KeyError:
            return False
        r = self._request("POST", "/v1/notifications/verify-webhook-signature", json=body)
        return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"

    def ping(self) -> float:
        start = time.perf_counter()
        self.access_token()
        return (time.perf_counter() - start) * 1000


def _err(r: httpx.Response) -> dict | None:
    try:
        body = r.json()
    except ValueError:
        return {"status": r.status_code}
    details = body.get("details") or [{}]
    return {"name": body.get("name"), "issue": details[0].get("issue"), "debug_id": body.get("debug_id"), "status": r.status_code}


def parse_capture(order: dict) -> CaptureResult:
    """Normalise an order (or capture response) into a CaptureResult. Accepts only what PayPal returned."""
    oid = order.get("id", "")
    try:
        cap = order["purchase_units"][0]["payments"]["captures"][0]
    except (KeyError, IndexError):
        status = order.get("status", "")
        return CaptureResult(status="failed" if status in ("VOIDED",) else "pending", order_id=oid, raw=order,
                             reason=f"Order status {status}" if status else None)
    st = cap.get("status", "")
    mapped = {"COMPLETED": "completed", "PENDING": "pending"}.get(st, "failed")
    method = "paypal" if "paypal" in (order.get("payment_source") or {}) else next(iter(order.get("payment_source") or {}), "paypal")
    return CaptureResult(status=mapped, order_id=oid, capture_id=cap.get("id"),
                         amount=Decimal(cap["amount"]["value"]), currency=cap["amount"]["currency_code"],
                         payment_method=method, reason=None if mapped != "failed" else f"Capture {st}", raw=order)
