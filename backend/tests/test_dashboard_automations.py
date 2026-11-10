import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.core.db import session_factory
from app.models import Customer, Invoice, Profile, Transaction
from app.services.automations import ensure_defaults, run_automation, run_due


def seed(user_id, rows):
    """rows: (days_ago, amount, status, provider, customer_name)"""
    db = session_factory()()
    uid = uuid.UUID(user_id)
    if not db.get(Profile, uid):
        db.add(Profile(id=uid, email="a@b.c"))
        db.flush()
    custs = {}
    for days, amount, status, provider, name in rows:
        c = custs.setdefault(name, Customer(user_id=uid, name=name))
        db.add(c)
        db.flush()
        t = Transaction(user_id=uid, customer_id=c.id, provider=provider, provider_order_id=f"O{uuid.uuid4().hex[:8]}", amount=Decimal(str(amount)),
                        currency="USD", status=status, events=[], created_at=datetime.now(timezone.utc) - timedelta(days=days, hours=1))
        db.add(t)
    db.commit()
    db.close()


def test_dashboard_calculations(user):
    seed(user.id, [(1, 500, "completed", "paypal", "Sarah"), (3, 250, "completed", "paypal", "Marcus"), (5, 100, "failed", "paypal", "Sarah"),
                   (6, 50, "refunded", "paypal", "Sarah"), (40, 200, "completed", "paypal", "Sarah"), (45, 100, "completed", "paypal", "Marcus")])
    d = user.get("/api/dashboard", params={"days": 30}).json()
    assert d["currency"] == "USD"
    assert d["revenue"]["total"] == 750 and d["revenue"]["previous"] == 300 and d["revenue"]["change_pct"] == 150.0
    assert len(d["revenue"]["series"]) == 30 and abs(sum(d["revenue"]["series"]) - 750) < 0.01 and len(d["revenue"]["previous_series"]) == 30
    assert d["revenue"]["daily_average"] == 25.0
    assert d["transactions"]["total"] == 4 and d["transactions"]["previous"] == 2
    assert d["success_rate"]["value"] == 66.7  # 2 completed / (2 + 1 failed)
    assert d["success_rate"]["failed_pct"] == 25.0 and d["success_rate"]["refunded_pct"] == 25.0
    assert len(d["recent_transactions"]) == 5 and d["recent_transactions"][0]["customer"]["name"] == "Sarah"
    assert d["paypal"]["mode"] == "sandbox" and d["paypal"]["configured"]
    kinds = {i["kind"] for i in d["insights"]}
    assert {"momentum", "pattern", "risk"} <= kinds
    assert user.get("/api/dashboard", params={"days": 365}).json()["revenue"]["granularity"] == "month"
    assert len(user.get("/api/dashboard", params={"days": 365}).json()["revenue"]["series"]) == 12
    assert user.get("/api/dashboard", params={"days": 0}).status_code == 422


def test_dashboard_empty_state_has_no_fake_numbers(user):
    d = user.get("/api/dashboard").json()
    assert d["revenue"]["total"] == 0 and d["transactions"]["total"] == 0 and d["success_rate"]["value"] is None
    assert d["pending"] == {"total": 0, "count": 0, "overdue_count": 0, "overdue_total": 0, "earliest_aging_days": None}
    assert d["recent_transactions"] == []


def test_dashboard_pending_and_overdue(user):
    for due, status in ((-4, "pending"), (10, "pending"), (-1, "paid")):
        inv = user.post("/api/invoices", json={"due_date": str(date.today() + timedelta(days=due)), "items": [{"description": "x", "unit_price": 100}]}).json()
        user.patch(f"/api/invoices/{inv['id']}", json={"status": "pending"})
        if status == "paid":  # only a verified capture may set paid -> use DB directly
            db = session_factory()()
            db.get(Invoice, uuid.UUID(inv["id"])).status = "paid"
            db.commit()
    p = user.get("/api/dashboard").json()["pending"]
    assert p["total"] == 200 and p["count"] == 2 and p["overdue_count"] == 1 and p["earliest_aging_days"] == 4


def test_data_is_user_scoped(user, other):
    seed(user.id, [(1, 500, "completed", "paypal", "Sarah")])
    assert other.get("/api/dashboard").json()["revenue"]["total"] == 0
    assert other.get("/api/transactions").json()["total"] == 0
    assert user.get("/api/transactions").json()["total"] == 1


def test_transactions_search_filter_sort_paginate_and_detail(user):
    seed(user.id, [(i, 100 + i, "completed" if i % 2 else "failed", "paypal" if i < 5 else "razorpay", "Sarah" if i % 3 else "Marcus") for i in range(1, 9)])
    assert user.get("/api/transactions", params={"page_size": 3}).json()["total"] == 8
    assert len(user.get("/api/transactions", params={"page_size": 3, "page": 3}).json()["items"]) == 2
    assert user.get("/api/transactions", params={"status": "failed"}).json()["total"] == 4
    assert user.get("/api/transactions", params={"provider": "razorpay"}).json()["total"] == 4
    assert user.get("/api/transactions", params={"search": "marc"}).json()["total"] == 2
    asc = [t["amount"] for t in user.get("/api/transactions", params={"sort": "amount", "order": "asc"}).json()["items"]]
    assert asc == sorted(asc)
    first = user.get("/api/transactions").json()["items"][0]
    assert user.get(f"/api/transactions/{first['id']}").json()["id"] == first["id"]
    assert user.get(f"/api/transactions/{uuid.uuid4()}").status_code == 404
    assert user.get("/api/transactions", params={"sort": "evil; drop"}).status_code == 422


def test_analytics_endpoint(user):
    seed(user.id, [(1, 300, "completed", "paypal", "Sarah"), (2, 100, "completed", "razorpay", "Sarah"), (3, 50, "completed", "paypal", "Marcus"), (4, 10, "failed", "paypal", "Marcus")])
    a = user.get("/api/analytics", params={"days": 30}).json()
    assert a["average_transaction_value"] == 150.0 and a["volume"]["total"] == 4
    assert a["top_customers"][0] == {"customer": "Sarah", "revenue": 400.0, "payments": 2}
    assert a["revenue_by_provider"] == {"paypal": 350.0, "razorpay": 100.0}


def test_automations_catalog_toggle_and_run(user):
    items = user.get("/api/automations").json()
    types = {a["automation_type"]: a for a in items}
    assert set(types) == {"daily_revenue_summary", "failed_payment_monitor", "overdue_reminder_suggestions", "payment_notifications"}
    assert not types["daily_revenue_summary"]["is_enabled"] and types["payment_notifications"]["is_enabled"]
    aid = types["overdue_reminder_suggestions"]["id"]
    assert user.patch(f"/api/automations/{aid}", json={"is_enabled": True}).json()["is_enabled"] is True
    inv = user.post("/api/invoices", json={"due_date": str(date.today() - timedelta(days=10)), "items": [{"description": "x", "unit_price": 60}]}).json()
    user.patch(f"/api/invoices/{inv['id']}", json={"status": "pending"})
    res = user.post(f"/api/automations/{aid}/run").json()
    assert "1 overdue" in res["result"]
    notes = user.get("/api/notifications").json()["items"]
    assert notes[0]["notification_type"] == "reminder_suggestion" and notes[0]["data"]["requires_approval"] is True
    user.post(f"/api/automations/{aid}/run")  # no duplicate suggestion within 2 days
    assert len([n for n in user.get("/api/notifications").json()["items"] if n["notification_type"] == "reminder_suggestion"]) == 1


def test_automation_ownership_and_scheduler(user, other):
    aid = user.get("/api/automations").json()[0]["id"]
    assert other.patch(f"/api/automations/{aid}", json={"is_enabled": True}).status_code == 404
    assert other.post(f"/api/automations/{aid}/run").status_code == 404
    user.patch(f"/api/automations/{aid}", json={"is_enabled": True})
    db = session_factory()()
    assert run_due(db) == 1
    assert run_due(db) == 0  # not due again so soon
    db.commit()


def test_notifications_read(user):
    seed(user.id, [(1, 1, "completed", "paypal", "A")])
    from app.services.common import notify
    db = session_factory()()
    notify(db, user.id, "t", "m")
    notify(db, user.id, "t2", "m2")
    db.commit()
    n = user.get("/api/notifications").json()
    assert n["unread"] == 2
    assert user.post(f"/api/notifications/{n['items'][0]['id']}/read").json()["is_read"] is True
    user.post("/api/notifications/read-all")
    assert user.get("/api/notifications").json()["unread"] == 0


def test_rate_limit_on_payment_creation(user):
    codes = [user.post("/api/payments/paypal/create-order", json={"amount": 5, "currency": "USD"}).status_code for _ in range(22)]
    assert codes.count(429) == 2 and codes[0] == 201


def test_error_format_and_no_internal_leak(user):
    r = user.get(f"/api/invoices/{uuid.uuid4()}")
    assert r.json() == {"error": {"code": "not_found", "message": "Invoice not found", "details": None}}
    bad = user.get("/api/invoices/not-a-uuid")
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_id"
