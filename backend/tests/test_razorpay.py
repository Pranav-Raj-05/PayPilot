import hashlib
import hmac
import json

from tests.conftest import RZP_SECRET, RZP_WEBHOOK, rzp_sig
from app.payments.razorpay.client import verify_payment_signature, verify_webhook_signature


def inr_invoice(user):
    return user.post("/api/invoices", json={"currency": "INR", "items": [{"description": "Consulting", "unit_price": 5000}]}).json()


def test_signature_helpers():
    sig = rzp_sig("order_1", "pay_1")
    assert verify_payment_signature("order_1", "pay_1", sig, RZP_SECRET)
    assert not verify_payment_signature("order_1", "pay_2", sig, RZP_SECRET)
    assert not verify_payment_signature("order_1", "pay_1", "", RZP_SECRET)
    body = b'{"a":1}'
    assert verify_webhook_signature(body, hmac.new(b"s", body, hashlib.sha256).hexdigest(), "s")
    assert not verify_webhook_signature(body, "bad", "s") and not verify_webhook_signature(body, "x", "")


def test_inr_only(user):
    usd = user.post("/api/invoices", json={"currency": "USD", "items": [{"description": "x", "unit_price": 5}]}).json()
    assert user.post("/api/payments/razorpay/create-order", json={"invoice_id": usd["id"]}).status_code == 400


def test_create_and_verify_flow(user, providers):
    inv = inr_invoice(user)
    r = user.post("/api/payments/razorpay/create-order", json={"invoice_id": inv["id"]}).json()
    assert r["amount_paise"] == 500000 and r["key_id"] == "rzp_test_abc" and "secret" not in json.dumps(r).lower()
    oid = r["order_id"]
    providers.rzp_payments["pay_1"] = {"id": "pay_1", "order_id": oid, "status": "captured", "amount": 500000, "currency": "INR", "method": "upi"}
    bad = user.post("/api/payments/razorpay/verify-payment", json={"razorpay_order_id": oid, "razorpay_payment_id": "pay_1", "razorpay_signature": "forged"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_signature"
    assert user.get(f"/api/invoices/{inv['id']}").json()["status"] == "pending"
    ok = user.post("/api/payments/razorpay/verify-payment", json={"razorpay_order_id": oid, "razorpay_payment_id": "pay_1", "razorpay_signature": rzp_sig(oid, "pay_1")}).json()
    assert ok["status"] == "completed" and ok["transaction"]["payment_method"] == "upi"
    assert user.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"
    again = user.post("/api/payments/razorpay/verify-payment", json={"razorpay_order_id": oid, "razorpay_payment_id": "pay_1", "razorpay_signature": rzp_sig(oid, "pay_1")}).json()
    assert again["status"] == "completed" and len(again["transaction"]["events"]) == len(ok["transaction"]["events"])


def test_valid_signature_but_wrong_amount_is_not_paid(user, providers):
    inv = inr_invoice(user)
    oid = user.post("/api/payments/razorpay/create-order", json={"invoice_id": inv["id"]}).json()["order_id"]
    providers.rzp_payments["pay_2"] = {"id": "pay_2", "order_id": oid, "status": "captured", "amount": 100, "currency": "INR", "method": "card"}
    r = user.post("/api/payments/razorpay/verify-payment", json={"razorpay_order_id": oid, "razorpay_payment_id": "pay_2", "razorpay_signature": rzp_sig(oid, "pay_2")}).json()
    assert r["status"] == "failed"
    assert user.get(f"/api/invoices/{inv['id']}").json()["status"] == "pending"


def test_other_user_cannot_verify(user, other, providers):
    inv = inr_invoice(user)
    oid = user.post("/api/payments/razorpay/create-order", json={"invoice_id": inv["id"]}).json()["order_id"]
    r = other.post("/api/payments/razorpay/verify-payment", json={"razorpay_order_id": oid, "razorpay_payment_id": "p", "razorpay_signature": rzp_sig(oid, "p")})
    assert r.status_code == 404


def test_webhook(client, user, providers):
    inv = inr_invoice(user)
    oid = user.post("/api/payments/razorpay/create-order", json={"invoice_id": inv["id"]}).json()["order_id"]
    payment = {"id": "pay_9", "order_id": oid, "status": "captured", "amount": 500000, "currency": "INR", "method": "upi"}
    event = {"event": "payment.captured", "payload": {"payment": {"entity": payment}}}
    raw = json.dumps(event).encode()
    sig = hmac.new(RZP_WEBHOOK.encode(), raw, hashlib.sha256).hexdigest()
    assert client.post("/api/webhooks/razorpay", content=raw, headers={"x-razorpay-signature": "bad"}).status_code == 401
    assert user.get(f"/api/invoices/{inv['id']}").json()["status"] == "pending"
    h = {"x-razorpay-signature": sig, "x-razorpay-event-id": "evt_1"}
    assert client.post("/api/webhooks/razorpay", content=raw, headers=h).json()["matched"] is True
    assert user.get(f"/api/invoices/{inv['id']}").json()["status"] == "paid"
    assert client.post("/api/webhooks/razorpay", content=raw, headers=h).json()["duplicate"] is True
