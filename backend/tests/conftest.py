import hashlib
import hmac
import json
import time
import uuid

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

SECRET = "test-secret-test-secret-test-secret-123"
RZP_SECRET = "rzp_secret"
RZP_WEBHOOK = "rzp_whsec"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
        "DATABASE_URL": "sqlite://", "SUPABASE_JWT_SECRET": SECRET, "PAYPAL_CLIENT_ID": "cid", "PAYPAL_CLIENT_SECRET": "csecret",
        "PAYPAL_BASE_URL": "https://api-m.sandbox.paypal.com", "PAYPAL_WEBHOOK_ID": "WH-TEST", "RAZORPAY_KEY_ID": "rzp_test_abc",
        "RAZORPAY_KEY_SECRET": RZP_SECRET, "RAZORPAY_WEBHOOK_SECRET": RZP_WEBHOOK, "SCHEDULER_ENABLED": "false",
        "FRONTEND_URL": "http://localhost:5173", "GEMINI_API_KEY": "", "OPENAI_API_KEY": "",
    }.items():
        monkeypatch.setenv(k, v)
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class FakeProviders:
    """Mock transport for PayPal + Razorpay so tests never touch the network."""

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.capture_calls = 0
        self.capture_mode = "completed"  # completed | declined | wrong_amount | not_approved
        self.webhook_valid = True
        self.rzp_payments: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 300})
        if path == "/v2/checkout/orders" and method == "POST":
            body = json.loads(request.content)
            unit = body["purchase_units"][0]
            oid = f"ORDER{len(self.orders) + 1:03d}"
            self.orders[oid] = {"id": oid, "status": "PAYER_ACTION_REQUIRED", "unit": unit, "request_id": request.headers.get("paypal-request-id")}
            return httpx.Response(201, json={"id": oid, "status": "PAYER_ACTION_REQUIRED", "links": [
                {"rel": "self", "href": f"x/{oid}"}, {"rel": "payer-action", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={oid}"}]})
        if path.startswith("/v2/checkout/orders/") and path.endswith("/capture"):
            self.capture_calls += 1
            oid = path.split("/")[-2]
            o = self.orders[oid]
            if self.capture_mode == "not_approved":
                return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_NOT_APPROVED"}]})
            if o["status"] == "COMPLETED":
                return httpx.Response(422, json={"name": "UNPROCESSABLE_ENTITY", "details": [{"issue": "ORDER_ALREADY_CAPTURED"}]})
            o["status"] = "COMPLETED"
            return httpx.Response(201, json=self._order_json(oid))
        if path.startswith("/v2/checkout/orders/") and method == "GET":
            oid = path.split("/")[-1]
            return httpx.Response(200, json=self._order_json(oid)) if oid in self.orders else httpx.Response(404, json={})
        if path == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS" if self.webhook_valid else "FAILURE"})
        # Razorpay
        if path == "/v1/orders":
            body = json.loads(request.content)
            oid = f"order_{len(self.orders) + 1}"
            self.orders[oid] = {"id": oid, **body}
            return httpx.Response(200, json={"id": oid, **body})
        if path.startswith("/v1/payments/"):
            pid = path.split("/")[-1]
            return httpx.Response(200, json=self.rzp_payments[pid]) if pid in self.rzp_payments else httpx.Response(404, json={})
        return httpx.Response(404, json={"error": path})

    def _order_json(self, oid):
        o = self.orders[oid]
        amt = o["unit"]["amount"]
        value = "1.00" if self.capture_mode == "wrong_amount" else amt["value"]
        out = {"id": oid, "status": o["status"], "payment_source": {"paypal": {}}}
        if o["status"] == "COMPLETED":
            out["purchase_units"] = [{"payments": {"captures": [{"id": f"CAP-{oid}", "status": "DECLINED" if self.capture_mode == "declined" else "COMPLETED",
                                                                  "amount": {"currency_code": amt["currency_code"], "value": value}}]}}]
        return out


@pytest.fixture
def providers(env):
    from app.api import deps
    from app.core.config import get_settings
    from app.payments.paypal.client import PayPalClient
    from app.payments.razorpay.client import RazorpayClient
    fp = FakeProviders()
    s = get_settings()
    deps._paypal = PayPalClient(s, http=httpx.Client(transport=httpx.MockTransport(fp.handler)))
    deps._razorpay = RazorpayClient(s, http=httpx.Client(transport=httpx.MockTransport(fp.handler)))
    yield fp
    deps._paypal = deps._razorpay = None


@pytest.fixture
def client(providers):
    from app.core import db as dbmod
    from app.core import ratelimit
    from app.models import Base
    from app.main import create_app
    engine = dbmod.init_engine("sqlite://")
    Base.metadata.create_all(engine)
    ratelimit.reset()
    with TestClient(create_app()) as c:
        yield c


def make_token(user_id: str | None = None, email="alex@example.com", secret=SECRET, exp=3600, aud="authenticated") -> str:
    return jwt.encode({"sub": user_id or str(uuid.uuid4()), "email": email, "aud": aud, "exp": int(time.time()) + exp,
                       "user_metadata": {"full_name": "Alex Vance"}}, secret, algorithm="HS256")


class Auth:
    def __init__(self, client: TestClient):
        self.id = str(uuid.uuid4())
        self.client = client
        self.headers = {"Authorization": f"Bearer {make_token(self.id)}"}

    def get(self, url, **kw): return self.client.get(url, headers=self.headers, **kw)
    def post(self, url, **kw): return self.client.post(url, headers=self.headers, **kw)
    def patch(self, url, **kw): return self.client.patch(url, headers=self.headers, **kw)
    def delete(self, url, **kw): return self.client.delete(url, headers=self.headers, **kw)


@pytest.fixture
def user(client):
    return Auth(client)


@pytest.fixture
def other(client):
    return Auth(client)


@pytest.fixture
def invoice(user):
    c = user.post("/api/customers", json={"name": "Sarah Johnson", "email": "sarah@acme.io"}).json()
    r = user.post("/api/invoices", json={"customer_id": c["id"], "description": "Website", "currency": "USD",
                                         "items": [{"description": "Website development", "quantity": 1, "unit_price": 500}]})
    assert r.status_code == 201, r.text
    return r.json()


def paypal_headers():
    return {"paypal-auth-algo": "SHA256withRSA", "paypal-cert-url": "https://api.paypal.com/cert", "paypal-transmission-id": "t1",
            "paypal-transmission-sig": "sig", "paypal-transmission-time": "2026-01-01T00:00:00Z"}


def rzp_sig(order_id, payment_id, secret=RZP_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
