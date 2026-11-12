import json

from tests.conftest import paypal_headers


def create(user, invoice):
    r = user.post("/api/payments/paypal/create-order", json={"invoice_id": invoice["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def test_create_order_uses_invoice_amount_not_client_amount(user, invoice, providers):
    r = user.post("/api/payments/paypal/create-order", json={"invoice_id": invoice["id"], "amount": 1, "currency": "EUR"}).json()
    assert r["transaction"]["amount"] == 500.0 and r["transaction"]["currency"] == "USD"
    assert r["approval_url"].startswith("https://www.sandbox.paypal.com/")
    sent = providers.orders[r["order_id"]]
    assert sent["unit"]["amount"] == {"currency_code": "USD", "value": "500.00"}
    assert sent["request_id"] == r["transaction"]["id"]  # idempotency key sent to PayPal
    inv = user.get(f"/api/invoices/{invoice['id']}").json()
    assert inv["status"] == "pending" and inv["payment_provider"] == "paypal" and inv["payment_link"] == r["approval_url"]


def test_create_order_reuses_open_order(user, invoice):
    a, b = create(user, invoice), create(user, invoice)
    assert a["order_id"] == b["order_id"]


def test_standalone_order_validation(user):
    assert user.post("/api/payments/paypal/create-order", json={"amount": 10}).status_code == 400
    assert user.post("/api/payments/paypal/create-order", json={"amount": -5, "currency": "USD"}).status_code == 422
    assert user.post("/api/payments/paypal/create-order", json={"amount": 10, "currency": "ZZZ"}).status_code == 422
    ok = user.post("/api/payments/paypal/create-order", json={"amount": 25, "currency": "usd", "description": "Consulting"})
    assert ok.status_code == 201 and ok.json()["transaction"]["invoice_id"] is None


def test_capture_marks_invoice_paid_only_after_verified_capture(user, invoice):
    o = create(user, invoice)
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"
    r = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()
    assert r["status"] == "completed" and r["transaction"]["provider_capture_id"] == f"CAP-{o['order_id']}"
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "paid"
    types = [e["type"] for e in r["transaction"]["events"]]
    assert types[:2] == ["order_created", "captured"] and "invoice_paid" in types
    assert user.get("/api/notifications").json()["unread"] >= 1


def test_duplicate_capture_is_idempotent(user, invoice, providers):
    o = create(user, invoice)
    first = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()
    second = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()
    assert first["status"] == second["status"] == "completed"
    assert providers.capture_calls == 1  # provider hit once
    assert len(first["transaction"]["events"]) == len(second["transaction"]["events"])


def test_cannot_capture_someone_elses_order(user, other, invoice, providers):
    o = create(user, invoice)
    assert other.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).status_code == 404
    assert other.get(f"/api/payments/paypal/order/{o['order_id']}").status_code == 404
    assert providers.capture_calls == 0
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"


def test_not_approved_does_not_mark_paid(user, invoice, providers):
    o = create(user, invoice)
    providers.capture_mode = "not_approved"
    r = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()
    assert r["status"] == "failed"
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"


def test_amount_mismatch_is_rejected(user, invoice, providers):
    o = create(user, invoice)
    providers.capture_mode = "wrong_amount"
    r = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()
    assert r["status"] == "failed" and "did not match" in r["transaction"]["failure_reason"]
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"


def test_declined_capture_fails_and_notifies(user, invoice, providers):
    o = create(user, invoice)
    providers.capture_mode = "declined"
    assert user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()["status"] == "failed"
    assert any(n["notification_type"] == "alert" for n in user.get("/api/notifications").json()["items"])


def test_paid_invoice_cannot_get_new_order(user, invoice):
    o = create(user, invoice)
    user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]})
    assert user.post("/api/payments/paypal/create-order", json={"invoice_id": invoice["id"]}).status_code == 409


def test_cancelled_link_cannot_be_captured(user, invoice, providers):
    o = create(user, invoice)
    user.post(f"/api/payment-links/{o['payment_link']['id']}/cancel")
    assert user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).status_code == 409
    assert providers.capture_calls == 0


def test_get_order_reconciles_completed_order(user, invoice, providers):
    o = create(user, invoice)
    providers.orders[o["order_id"]]["status"] = "COMPLETED"  # captured at PayPal but we missed it
    r = user.get(f"/api/payments/paypal/order/{o['order_id']}").json()
    assert r["paypal_status"] == "COMPLETED" and r["transaction"]["status"] == "completed"
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "paid"


def test_public_capture_and_cancel(client, user, invoice):
    o = create(user, invoice)
    assert client.post("/api/public/pay/paypal/capture", json={"order_id": "NOPE123"}).status_code == 404
    pub = client.post("/api/public/pay/paypal/capture", json={"order_id": o["order_id"]})
    assert pub.status_code == 200 and pub.json()["status"] == "completed" and "provider_order_id" not in pub.json()
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "paid"


def test_status_endpoint(user):
    s = user.get("/api/payments/paypal/status").json()
    assert s["configured"] and s["connected"] and s["mode"] == "sandbox"
    assert s["client_id_hint"].endswith("cid") and s["client_id_hint"] != "cid"  # masked
    assert "csecret" not in json.dumps(s)


# ----------------------------------------------------------------- webhooks
def hook(client, event, headers=None):
    return client.post("/api/webhooks/paypal", content=json.dumps(event), headers={**(headers or paypal_headers()), "content-type": "application/json"})


def completed_event(order_id, eid="WH-EVT-1"):
    return {"id": eid, "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"id": f"CAP-{order_id}", "supplemental_data": {"related_ids": {"order_id": order_id}}}}


def test_webhook_rejects_invalid_signature(client, user, invoice, providers):
    o = create(user, invoice)
    providers.webhook_valid = False
    assert hook(client, completed_event(o["order_id"])).status_code == 401
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"
    assert client.post("/api/webhooks/paypal", content="{}", headers={"content-type": "application/json"}).status_code == 401  # missing headers


def test_webhook_completes_payment_and_dedupes_retries(client, user, invoice, providers):
    o = create(user, invoice)
    providers.orders[o["order_id"]]["status"] = "COMPLETED"  # PayPal captured it (customer flow)
    r = hook(client, completed_event(o["order_id"]))
    assert r.status_code == 200 and r.json()["matched"]
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "paid"
    retry = hook(client, completed_event(o["order_id"]))
    assert retry.json()["duplicate"] is True
    txn = user.get("/api/transactions").json()["items"][0]
    assert [e["type"] for e in txn["events"]].count("captured") == 1
    assert user.get("/api/payments/paypal/status").json()["last_webhook_at"]


def test_webhook_reverifies_with_paypal_and_ignores_unknown_orders(client, user, invoice, providers):
    o = create(user, invoice)  # webhook claims completed but PayPal order is NOT captured
    r = hook(client, completed_event(o["order_id"], "WH-EVT-2"))
    assert r.status_code == 200
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"
    assert hook(client, completed_event("UNKNOWN", "WH-EVT-3")).json()["matched"] is False


def test_webhook_refund(client, user, invoice, providers):
    o = create(user, invoice)
    user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]})
    # The webhook body is only a pointer; the amount is read back from PayPal, so the refund must exist there.
    providers.refunds["REF1"] = {"id": "REF1", "status": "COMPLETED", "amount": {"value": "500.00", "currency_code": "USD"}}
    ev = {"id": "WH-EVT-9", "event_type": "PAYMENT.CAPTURE.REFUNDED",
          "resource": {"id": "REF1", "links": [{"rel": "up", "href": f"https://api/v2/payments/captures/CAP-{o['order_id']}"}]}}
    assert hook(client, ev).status_code == 200
    assert user.get("/api/transactions").json()["items"][0]["status"] == "refunded"
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"


def test_capture_records_fee_and_net(user, invoice):
    o = create(user, invoice)
    t = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()["transaction"]
    assert t["fee_amount"] == 14.8 and t["net_amount"] == 485.2


def test_refund_flow_idempotent_and_owned(user, other, invoice, providers):
    o = create(user, invoice)
    t = user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]}).json()["transaction"]
    assert other.post(f"/api/transactions/{t['id']}/refund").status_code == 404
    r = user.post(f"/api/transactions/{t['id']}/refund").json()
    assert r["status"] == "refunded"
    assert user.post(f"/api/transactions/{t['id']}/refund").json()["status"] == "refunded"
    assert providers.refund_calls == 1
    assert user.get(f"/api/invoices/{invoice['id']}").json()["status"] == "pending"


def test_refund_only_completed(user, invoice):
    o = create(user, invoice)
    tid = user.get("/api/transactions").json()["items"][0]["id"]
    assert user.post(f"/api/transactions/{tid}/refund").status_code == 409


def test_summaries_and_activity(user, invoice):
    o = create(user, invoice)
    s = user.get("/api/invoices/summary").json()
    assert s["counts"]["pending"] == 1 and s["pending"]["total"] == 500 and s["total_invoiced"] == 500
    act = user.get(f"/api/invoices/{invoice['id']}/activity").json()
    assert [a["title"] for a in act][0] == "Invoice created" and act[-1]["done"] is False
    user.post("/api/payments/paypal/capture-order", json={"order_id": o["order_id"]})
    assert user.get("/api/invoices/summary").json()["paid"] == {"total": 500, "count": 1}
    ts = user.get("/api/transactions/summary").json()
    assert ts["settled"]["total"] == 500 and ts["settled"]["rate"] == 100.0
    ls = user.get("/api/payment-links/summary").json()
    assert ls["total"] == 1 and ls["paid"] == 1 and ls["conversion_rate"] == 100.0
