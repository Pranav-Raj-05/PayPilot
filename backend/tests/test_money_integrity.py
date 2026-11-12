"""Stage A - money integrity regression tests (D1-D5). Providers are mocked (tests/conftest.py FakeProviders); nothing here can reach PayPal or Razorpay.

Each section starts with the test that reproduces the original defect, then covers success, failure, duplicate and ownership cases."""
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.conftest import Auth, paypal_headers
from tests.test_profile_concurrency import postgres_client  # noqa: F401  (fixture: skips with an explanatory message unless TEST_POSTGRES_URL and ALLOW_DESTRUCTIVE_PG_TESTS=1 are set; uses a throw-away database)


# ------------------------------------------------------------------ helpers
def make_invoice(user, amount=100, currency="USD"):
    r = user.post("/api/invoices", json={"currency": currency, "tax_rate": 0, "items": [{"description": "Design", "quantity": 1, "unit_price": amount}]})
    assert r.status_code == 201, r.text
    return r.json()


def make_link(user, inv, provider="paypal"):
    r = user.post(f"/api/invoices/{inv['id']}/payment-link", params={"provider": provider})
    assert r.status_code == 201, r.text
    return r.json()


def pay(client, order_id):
    """What the customer's return page does (public, unauthenticated)."""
    return client.post("/api/public/pay/paypal/capture", json={"order_id": order_id})


def hook(client, event, headers=None):
    return client.post("/api/webhooks/paypal", content=json.dumps(event), headers={**(headers or paypal_headers()), "content-type": "application/json"})


def txns(user):
    return user.get("/api/transactions").json()["items"]


def inv_get(user, inv):
    return user.get(f"/api/invoices/{inv['id']}").json()


def titles(user):
    return [n["title"] for n in user.get("/api/notifications").json()["items"]]


def refund(user, txn_id, **body):
    return user.post(f"/api/transactions/{txn_id}/refund", json=body)


def paid_invoice(user, client, amount=500):
    """An invoice paid in full through a real (mocked) PayPal capture. Returns (invoice, order_id, transaction)."""
    inv = make_invoice(user, amount)
    order = make_link(user, inv)["provider_reference"]
    assert pay(client, order).json()["status"] == "completed"
    return inv, order, txns(user)[0]


def refund_event(order_id, refund_id, eid):
    return {"id": eid, "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": refund_id, "supplemental_data": {"related_ids": {"order_id": order_id}},
                                                                                "links": [{"rel": "up", "href": f"https://api/v2/payments/captures/CAP-{order_id}"}]}}


def tamper_invoice(invoice_id, **changes):
    """Change an invoice directly in the database, bypassing the API (simulates legacy rows / a bug elsewhere)."""
    from app.core.db import session_factory
    from app.models import Invoice
    with session_factory()() as db:
        inv = db.get(Invoice, uuid.UUID(invoice_id))
        for k, v in changes.items():
            setattr(inv, k, v)
        db.commit()


# ================================================================== D1: invoice changed after a payment link exists
class TestD1InvoiceChangedAfterLink:
    def test_original_scenario_can_no_longer_happen(self, client, user, providers):
        """Was: $100 invoice + link, edit to $1000, capture the $100 order -> invoice $1000 marked paid."""
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        r = user.patch(f"/api/invoices/{inv['id']}", json={"status": "draft"})
        assert r.status_code == 409 and r.json()["error"]["code"] == "payment_link_active"
        r = user.patch(f"/api/invoices/{inv['id']}", json={"items": [{"description": "Design", "quantity": 1, "unit_price": 1000}]})
        assert r.status_code == 409 and r.json()["error"]["code"] == "payment_link_active"
        assert inv_get(user, inv)["total_amount"] == 100  # nothing changed
        assert pay(client, order).json()["status"] == "completed"
        assert inv_get(user, inv)["status"] == "paid" and inv_get(user, inv)["total_amount"] == 100

    @pytest.mark.parametrize("change", [{"currency": "EUR"}, {"tax_rate": 20}, {"description": "other"}, {"due_date": "2030-01-01"}])
    def test_any_content_edit_is_blocked_while_the_link_is_live(self, user, providers, change):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        assert user.patch(f"/api/invoices/{inv['id']}", json=change).status_code == 409

    def test_editing_is_possible_after_the_link_is_cancelled_and_the_old_order_stays_dead(self, client, user, providers):
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        old_order = link["provider_reference"]
        assert user.post(f"/api/payment-links/{link['id']}/cancel").status_code == 200
        assert user.patch(f"/api/invoices/{inv['id']}", json={"status": "draft"}).status_code == 200
        edited = user.patch(f"/api/invoices/{inv['id']}", json={"items": [{"description": "Design", "quantity": 1, "unit_price": 1000}]}).json()
        assert edited["total_amount"] == 1000 and edited["version"] == inv["version"] + 1
        captures_before = providers.capture_calls
        r = pay(client, old_order)  # the old $100 order
        assert r.status_code == 409
        assert providers.capture_calls == captures_before  # refused BEFORE PayPal was asked to capture anything
        assert inv_get(user, inv)["status"] == "draft"
        new_order = make_link(user, inv)["provider_reference"]
        assert new_order != old_order and providers.orders[new_order]["unit"]["amount"]["value"] == "1000.00"
        assert pay(client, new_order).json()["status"] == "completed"
        final = inv_get(user, inv)
        assert final["status"] == "paid" and final["total_amount"] == 1000
        assert [t["amount"] for t in txns(user) if t["status"] == "completed"] == [1000.0]

    def test_orders_remember_the_invoice_version(self, user, providers):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        assert txns(user)[0]["id"]
        from app.core.db import session_factory
        from app.models import Transaction
        with session_factory()() as db:
            assert db.scalars(select(Transaction)).first().invoice_version == inv["version"]

    def test_capture_is_refused_before_any_money_moves_when_the_invoice_no_longer_matches(self, client, user, providers):
        """Defence in depth for rows the API rules did not protect (legacy data, direct DB edits)."""
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        tamper_invoice(inv["id"], total_amount=Decimal("1000.00"), version=2)
        r = pay(client, order)
        assert r.status_code == 409 and "no longer valid" in r.json()["error"]["message"]
        assert providers.capture_calls == 0
        assert inv_get(user, inv)["status"] != "paid"
        assert txns(user)[0]["status"] == "cancelled"  # the stale order was voided and stays voided

    def test_amount_change_with_same_version_is_still_caught(self, client, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        tamper_invoice(inv["id"], total_amount=Decimal("250.00"))
        assert pay(client, order).status_code == 409 and providers.capture_calls == 0

    def test_currency_change_is_caught(self, client, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        tamper_invoice(inv["id"], currency="EUR")
        assert pay(client, order).status_code == 409 and providers.capture_calls == 0

    def test_a_payment_that_arrives_anyway_is_recorded_but_never_pays_the_changed_invoice(self, client, user, providers):
        """PayPal captured the old order on its own (webhook path): the money is real, so it is recorded, but the invoice is not paid."""
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        tamper_invoice(inv["id"], total_amount=Decimal("1000.00"), version=2)
        providers.orders[order]["status"] = "COMPLETED"
        event = {"id": "WH-D1-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": f"CAP-{order}", "supplemental_data": {"related_ids": {"order_id": order}}}}
        assert hook(client, event).status_code == 200
        t = txns(user)[0]
        assert t["status"] == "completed" and t["amount"] == 100  # the $100 that really moved
        assert "payment_unmatched" in [e["type"] for e in t["events"]]
        assert inv_get(user, inv)["status"] != "paid"
        assert "Payment needs review" in titles(user)

    def test_one_live_order_per_invoice_across_providers(self, user, providers):
        inv = make_invoice(user, 5000, "INR")
        a = make_link(user, inv, "razorpay")
        assert make_link(user, inv, "razorpay")["id"] == a["id"]  # same provider: reused
        r = user.post(f"/api/invoices/{inv['id']}/payment-link", params={"provider": "paypal"})
        assert r.status_code == 409 and "already has an active razorpay" in r.json()["error"]["message"]

    def test_deleting_a_draft_with_a_live_order_is_refused(self, user, providers):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        tamper_invoice(inv["id"], status="draft")  # legacy state: draft with a live link
        assert user.delete(f"/api/invoices/{inv['id']}").status_code == 409

    def test_link_ownership_is_enforced(self, client, user, other, providers):
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        assert other.post(f"/api/payment-links/{link['id']}/cancel").status_code == 404
        assert other.post("/api/payments/paypal/capture-order", json={"order_id": link["provider_reference"]}).status_code == 404
        assert other.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).status_code == 404

    def test_a_link_that_does_not_belong_to_its_order_is_rejected(self, client, user, providers):
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        from app.core.db import session_factory
        from app.models import PaymentLink
        with session_factory()() as db:
            db.get(PaymentLink, uuid.UUID(link["id"])).invoice_id = None
            db.commit()
        assert pay(client, link["provider_reference"]).status_code == 409 and providers.capture_calls == 0

    def test_duplicate_capture_after_success_does_not_capture_again(self, client, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        assert pay(client, order).status_code == 200
        assert pay(client, order).json()["status"] == "completed"
        assert providers.capture_calls == 1


# ================================================================== D2: cancelled invoice with a payment link
class TestD2CancelledInvoice:
    def test_original_scenario_can_no_longer_happen(self, client, user, providers):
        """Was: cancel the invoice, pay through the old link -> capture succeeded and the invoice flipped to paid."""
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        assert user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).status_code == 200
        r = pay(client, link["provider_reference"])
        assert r.status_code == 409
        assert providers.capture_calls == 0
        assert inv_get(user, inv)["status"] == "cancelled"

    def test_cancelling_the_invoice_cancels_the_link_and_the_order_together(self, user, providers):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        cancelled = user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).json()
        assert cancelled["status"] == "cancelled" and cancelled["payment_link"] is None
        assert user.get("/api/payment-links").json()[0]["status"] == "cancelled"
        t = txns(user)[0]
        assert t["status"] == "cancelled" and "cancelled" in [e["type"] for e in t["events"]]

    def test_the_authenticated_capture_endpoint_is_closed_too(self, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"})
        assert user.post("/api/payments/paypal/capture-order", json={"order_id": order}).status_code == 409 and providers.capture_calls == 0

    def test_a_paid_invoice_cannot_be_cancelled(self, client, user, providers):
        inv = make_invoice(user, 100)
        pay(client, make_link(user, inv)["provider_reference"])
        r = user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"})
        assert r.status_code == 409 and inv_get(user, inv)["status"] == "paid"

    def test_cancelling_twice_is_harmless(self, user, providers):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        assert user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).status_code == 200
        assert user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).status_code == 200

    def test_a_cancelled_invoice_cannot_get_a_new_link(self, user, providers):
        inv = make_invoice(user, 100)
        user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"})
        assert user.post(f"/api/invoices/{inv['id']}/payment-link").status_code == 409

    def test_money_that_arrives_for_a_cancelled_invoice_is_recorded_and_flagged_not_applied(self, client, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"})
        providers.orders[order]["status"] = "COMPLETED"  # the customer had already paid at PayPal
        event = {"id": "WH-D2-1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": f"CAP-{order}", "supplemental_data": {"related_ids": {"order_id": order}}}}
        assert hook(client, event).status_code == 200
        t = txns(user)[0]
        assert t["status"] == "completed" and "payment_unmatched" in [e["type"] for e in t["events"]]
        assert inv_get(user, inv)["status"] == "cancelled"  # never resurrected as paid
        assert "Payment needs review" in titles(user)

    def test_cancelling_a_payment_link_cancels_its_order_and_blocks_capture(self, client, user, providers):
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        assert user.post(f"/api/payment-links/{link['id']}/cancel").json()["status"] == "cancelled"
        assert txns(user)[0]["status"] == "cancelled"
        assert inv_get(user, inv)["payment_link"] is None and inv_get(user, inv)["status"] == "pending"
        assert pay(client, link["provider_reference"]).status_code == 409 and providers.capture_calls == 0
        assert user.post(f"/api/payment-links/{link['id']}/cancel").status_code == 409  # already cancelled

    def test_a_paid_link_cannot_be_cancelled(self, client, user, providers):
        inv = make_invoice(user, 100)
        link = make_link(user, inv)
        pay(client, link["provider_reference"])
        assert user.post(f"/api/payment-links/{link['id']}/cancel").status_code == 409
        assert inv_get(user, inv)["status"] == "paid"

    def test_cancel_then_capture_and_capture_then_cancel_are_both_consistent(self, client, user, providers):
        """The two orders a race can resolve to. Neither may leave a paid invoice that is also cancelled."""
        a, b = make_invoice(user, 100), make_invoice(user, 100)
        order_a, order_b = make_link(user, a)["provider_reference"], make_link(user, b)["provider_reference"]
        # cancel wins
        assert user.patch(f"/api/invoices/{a['id']}", json={"status": "cancelled"}).status_code == 200
        assert pay(client, order_a).status_code == 409 and inv_get(user, a)["status"] == "cancelled"
        # capture wins
        assert pay(client, order_b).status_code == 200
        assert user.patch(f"/api/invoices/{b['id']}", json={"status": "cancelled"}).status_code == 409 and inv_get(user, b)["status"] == "paid"

    def test_public_cancel_by_the_customer_still_works(self, client, user, providers):
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        assert client.post("/api/public/pay/paypal/cancel", json={"order_id": order}).json()["status"] == "cancelled"
        assert pay(client, order).status_code == 409


def _race_once(client, user, providers):
    inv = make_invoice(user, 100)
    order = make_link(user, inv)["provider_reference"]
    barrier = threading.Barrier(2)
    out = {}

    def cancel():
        barrier.wait()
        out["cancel"] = user.patch(f"/api/invoices/{inv['id']}", json={"status": "cancelled"}).status_code

    def capture():
        barrier.wait()
        out["capture"] = pay(client, order).status_code

    with ThreadPoolExecutor(2) as ex:
        for f in [ex.submit(cancel), ex.submit(capture)]:
            f.result()
    final, t = inv_get(user, inv), next(x for x in txns(user))
    # Exactly one side won, and the final state agrees with the winner.
    if out["capture"] == 200:
        assert final["status"] == "paid" and t["status"] == "completed" and out["cancel"] == 409, (out, final["status"], t["status"])
    else:
        assert final["status"] == "cancelled" and t["status"] == "cancelled" and out["cancel"] == 200 and out["capture"] == 409, (out, final["status"], t["status"])
    assert not (final["status"] == "paid" and t["status"] != "completed")


def test_d2_cancel_and_capture_race_on_postgresql(postgres_client, providers):  # noqa: F811
    """Fires cancel and capture at the same instant, repeatedly, on a throw-away PostgreSQL database (needs the PostgreSQL env; skips otherwise)."""
    user = Auth(postgres_client)
    for _ in range(15):
        _race_once(postgres_client, user, providers)


# ================================================================== D3: partial refunds
class TestD3PartialRefunds:
    def test_original_scenario_a_partial_refund_is_no_longer_a_full_refund(self, client, user, providers):
        """Was: a $5 refund of a $100 payment marked the whole payment refunded and reopened the invoice."""
        inv, order, t = paid_invoice(user, client, 100)
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "5.00", "currency_code": "USD"}}
        assert hook(client, refund_event(order, "EXT1", "WH-D3-1")).status_code == 200
        t = txns(user)[0]
        assert t["status"] == "partially_refunded" and t["amount"] == 100 and t["refunded_amount"] == 5
        assert inv_get(user, inv)["status"] == "paid"  # NOT reopened

    def test_api_partial_refund_keeps_the_original_amount_and_records_one_row(self, client, user, providers):
        inv, _, t = paid_invoice(user, client, 500)
        r = refund(user, t["id"], amount=120).json()
        assert r["status"] == "partially_refunded" and r["amount"] == 500 and r["refunded_amount"] == 120
        assert [(x["amount"], x["status"], x["source"]) for x in r["refunds"]] == [(120.0, "completed", "api")]
        assert providers.refund_requests[-1]["body"]["amount"] == {"value": "120.00", "currency_code": "USD"}
        assert inv_get(user, inv)["status"] == "paid"

    def test_cumulative_refunds_reach_full_and_only_then_reopen_the_invoice(self, client, user, providers):
        inv, _, t = paid_invoice(user, client, 500)
        assert refund(user, t["id"], amount=200).json()["status"] == "partially_refunded"
        r = refund(user, t["id"], amount=200).json()
        assert r["status"] == "partially_refunded" and r["refunded_amount"] == 400 and inv_get(user, inv)["status"] == "paid"
        r = refund(user, t["id"]).json()  # no amount = everything still refundable (100)
        assert providers.refund_requests[-1]["body"]["amount"]["value"] == "100.00"
        assert r["status"] == "refunded" and r["refunded_amount"] == 500 and len(r["refunds"]) == 3 and r["amount"] == 500
        assert inv_get(user, inv)["status"] == "pending"  # full refund keeps the previous behaviour

    def test_default_refund_after_a_partial_one_refunds_only_the_remainder(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100)
        refund(user, t["id"])
        assert providers.refund_requests[-1]["body"]["amount"]["value"] == "400.00"

    @pytest.mark.parametrize("amount,code", [(600, 422), (0, 422), (-5, 422), (500.01, 422)])
    def test_invalid_amounts_are_rejected_without_calling_the_provider(self, client, user, providers, amount, code):
        _, _, t = paid_invoice(user, client, 500)
        assert refund(user, t["id"], amount=amount).status_code == code
        assert providers.refund_requests == []

    def test_cannot_refund_more_than_what_is_left(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=400)
        assert refund(user, t["id"], amount=200).status_code == 422
        assert len(providers.refund_requests) == 1

    def test_the_same_idempotency_key_never_refunds_twice(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        key = "retry-key-0001"
        a = refund(user, t["id"], amount=50, idempotency_key=key).json()
        b = refund(user, t["id"], amount=50, idempotency_key=key).json()
        assert a["refunded_amount"] == b["refunded_amount"] == 50 and len(b["refunds"]) == 1 and providers.refund_calls == 1

    def test_idempotency_key_header_is_honoured_too(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        h = {**user.headers, "Idempotency-Key": "header-key-0001"}
        client.post(f"/api/transactions/{t['id']}/refund", json={"amount": 50}, headers=h)
        client.post(f"/api/transactions/{t['id']}/refund", json={"amount": 50}, headers=h)
        assert providers.refund_calls == 1

    def test_repeating_a_full_refund_does_not_call_the_provider_again(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        assert refund(user, t["id"]).json()["status"] == "refunded"
        assert refund(user, t["id"]).json()["status"] == "refunded"
        assert providers.refund_calls == 1

    def test_a_pending_refund_is_reserved_and_blocks_a_second_full_refund(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        providers.refund_status = "PENDING"
        r = refund(user, t["id"]).json()
        assert r["status"] == "completed" and r["refunded_amount"] == 0 and [x["status"] for x in r["refunds"]] == ["pending"]
        assert refund(user, t["id"]).json()["status"] == "completed"  # returned unchanged
        assert providers.refund_calls == 1
        assert refund(user, t["id"], amount=10).status_code == 409  # nothing left while 500 is in flight

    def test_a_pending_refund_settles_through_the_webhook_and_is_counted_once(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        providers.refund_status = "PENDING"
        refund(user, t["id"])
        providers.refunds["REF1"]["status"] = "COMPLETED"  # PayPal finished it
        assert hook(client, refund_event(order, "REF1", "WH-D3-2")).status_code == 200
        r = txns(user)[0]
        assert r["status"] == "refunded" and r["refunded_amount"] == 500 and len(r["refunds"]) == 1 and r["refunds"][0]["status"] == "completed"

    def test_duplicate_and_replayed_webhooks_never_double_count(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "USD"}}
        assert hook(client, refund_event(order, "EXT1", "WH-A")).json()["matched"] is True
        assert hook(client, refund_event(order, "EXT1", "WH-A")).json()["duplicate"] is True  # same delivery retried
        assert hook(client, refund_event(order, "EXT1", "WH-B")).status_code == 200  # same refund, new event id
        r = txns(user)[0]
        assert r["refunded_amount"] == 100 and len(r["refunds"]) == 1

    def test_an_api_refund_followed_by_its_own_webhook_is_counted_once(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100)  # creates REF1 at PayPal and records it
        assert hook(client, refund_event(order, "REF1", "WH-ECHO")).status_code == 200  # PayPal's confirmation of the same refund
        r = txns(user)[0]
        assert r["refunded_amount"] == 100 and len(r["refunds"]) == 1

    def test_the_webhook_body_amount_is_never_trusted(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "5.00", "currency_code": "USD"}}
        ev = refund_event(order, "EXT1", "WH-LIE")
        ev["resource"]["amount"] = {"value": "500.00", "currency_code": "USD"}  # a forged/mistaken body claims a full refund
        hook(client, ev)
        assert txns(user)[0]["refunded_amount"] == 5

    def test_a_refund_in_another_currency_is_flagged_and_not_applied(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "50.00", "currency_code": "EUR"}}
        hook(client, refund_event(order, "EXT1", "WH-CUR"))
        r = txns(user)[0]
        assert r["status"] == "completed" and r["refunded_amount"] == 0 and r["refunds"] == []
        assert "refund_currency_mismatch" in [e["type"] for e in r["events"]] and "Refund needs review" in titles(user)

    def test_a_refund_larger_than_the_payment_is_flagged_and_not_applied(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "600.00", "currency_code": "USD"}}
        hook(client, refund_event(order, "EXT1", "WH-BIG"))
        r = txns(user)[0]
        assert r["refunded_amount"] == 0 and "refund_exceeds_payment" in [e["type"] for e in r["events"]]

    def test_two_external_refunds_that_together_exceed_the_payment_flag_the_second(self, client, user, providers):
        _, order, t = paid_invoice(user, client, 500)
        for rid, eid in (("EXT1", "WH-1"), ("EXT2", "WH-2")):
            providers.refunds[rid] = {"id": rid, "status": "COMPLETED", "amount": {"value": "300.00", "currency_code": "USD"}}
            hook(client, refund_event(order, rid, eid))
        r = txns(user)[0]
        assert r["refunded_amount"] == 300 and r["status"] == "partially_refunded" and len(r["refunds"]) == 1

    def test_refund_event_arriving_before_the_capture_is_reconciled_first(self, client, user, providers):
        """Out of order: PayPal already captured and refunded, but we only hear about the refund. The payment is reconciled first."""
        inv = make_invoice(user, 500)
        order = make_link(user, inv)["provider_reference"]
        providers.orders[order]["status"] = "COMPLETED"
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "USD"}}
        assert hook(client, refund_event(order, "EXT1", "WH-OOO")).status_code == 200
        t = txns(user)[0]
        assert t["status"] == "partially_refunded" and t["refunded_amount"] == 100 and inv_get(user, inv)["status"] == "paid"

    def test_refund_event_for_an_unsettled_payment_is_rejected_so_the_provider_retries(self, client, user, providers):
        inv = make_invoice(user, 500)
        order = make_link(user, inv)["provider_reference"]  # PayPal order exists but is NOT captured
        providers.refunds["EXT1"] = {"id": "EXT1", "status": "COMPLETED", "amount": {"value": "100.00", "currency_code": "USD"}}
        ev = refund_event(order, "EXT1", "WH-RETRY")
        assert hook(client, ev).status_code == 409
        providers.orders[order]["status"] = "COMPLETED"  # the capture is now visible
        r = hook(client, ev)  # the very same event id is processed on the retry (the dedupe row was rolled back)
        assert r.status_code == 200 and r.json().get("duplicate") is None
        assert txns(user)[0]["refunded_amount"] == 100

    def test_reversal_is_recorded_once_as_a_full_refund_of_the_remainder(self, client, user, providers):
        inv, order, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100)
        ev = {"id": "WH-REV", "event_type": "PAYMENT.CAPTURE.REVERSED", "resource": {"id": f"CAP-{order}", "supplemental_data": {"related_ids": {"order_id": order}}}}
        assert hook(client, ev).status_code == 200
        assert hook(client, {**ev, "id": "WH-REV-2"}).status_code == 200  # a second reversal event changes nothing
        r = txns(user)[0]
        assert r["status"] == "refunded" and r["refunded_amount"] == 500
        assert sorted(x["amount"] for x in r["refunds"]) == [100.0, 400.0]

    def test_refund_history_is_part_of_the_audit_trail(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100, reason="goodwill")
        r = user.get(f"/api/transactions/{t['id']}").json()
        types = [e["type"] for e in r["events"]]
        assert "refund_completed" in types and r["refunds"][0]["reason"] == "goodwill" and r["refunds"][0]["provider_refund_id"]
        from app.core.db import session_factory
        from app.models import AuditLog
        with session_factory()() as db:
            assert any(a.action == "paypal.refund" for a in db.scalars(select(AuditLog)).all())

    def test_ownership_and_provider_rules(self, client, user, other, providers):
        _, _, t = paid_invoice(user, client, 500)
        assert refund(other, t["id"], amount=10).status_code == 404
        assert other.get(f"/api/transactions/{t['id']}").status_code == 404
        # a Razorpay payment cannot be refunded here yet: clear error, not a silent success
        inv = make_invoice(user, 5000, "INR")
        link = make_link(user, inv, "razorpay")
        rz = next(x for x in txns(user) if x["provider"] == "razorpay")
        assert refund(user, rz["id"]).status_code in (400, 409)

    def test_only_settled_payments_can_be_refunded(self, user, providers):
        inv = make_invoice(user, 100)
        make_link(user, inv)
        assert refund(user, txns(user)[0]["id"]).status_code == 409

    def test_partial_refunds_reduce_reported_revenue_by_the_refunded_amount(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100)
        assert user.get("/api/dashboard").json()["revenue"]["total"] == 400
        s = user.get("/api/transactions/summary").json()
        assert s["settled"]["total"] == 400 and s["refunded"]["total"] == 100 and s["refunded"]["count"] == 1

    def test_database_rejects_a_duplicate_provider_refund_id(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100)
        from app.core.db import session_factory
        from app.models import Refund
        with session_factory()() as db:
            first = db.scalars(select(Refund)).first()
            db.add(Refund(user_id=first.user_id, transaction_id=first.transaction_id, provider=first.provider, provider_refund_id=first.provider_refund_id,
                          amount=Decimal("1.00"), currency="USD"))
            with pytest.raises(IntegrityError):
                db.commit()

    def test_concurrent_identical_refund_requests_send_one_provider_request_id(self, client, user, providers):
        """Two identical default refunds carry the same PayPal-Request-Id, so PayPal itself would replay rather than refund twice."""
        _, _, t = paid_invoice(user, client, 500)
        refund(user, t["id"], amount=100, idempotency_key="same-intent-1")
        ids = {r["request_id"] for r in providers.refund_requests}
        assert len(ids) == 1 and next(iter(ids)).startswith("refund-")


# ================================================================== D4: payment notifications switch
def _toggle(user, enabled):
    pn = next(a for a in user.get("/api/automations").json() if a["automation_type"] == "payment_notifications")
    assert user.patch(f"/api/automations/{pn['id']}", json={"is_enabled": enabled}).status_code == 200
    return pn


class TestD4PaymentNotificationToggle:
    def test_original_scenario_switching_it_off_now_stops_payment_notifications(self, client, user, providers):
        _toggle(user, False)
        paid_invoice(user, client, 100)
        assert "Payment received" not in titles(user)

    def test_on_by_default_even_if_the_user_never_opened_automations(self, client, user, providers):
        paid_invoice(user, client, 100)
        assert "Payment received" in titles(user)

    def test_enabled_explicitly(self, client, user, providers):
        _toggle(user, False)
        _toggle(user, True)
        paid_invoice(user, client, 100)
        assert "Payment received" in titles(user)

    def test_off_also_covers_failed_and_refunded_notifications(self, client, user, providers):
        _toggle(user, False)
        inv = make_invoice(user, 100)
        providers.capture_mode = "declined"
        pay(client, make_link(user, inv)["provider_reference"])
        assert "Payment failed" not in titles(user)
        providers.capture_mode = "completed"
        _, _, t = paid_invoice(user, client, 200)
        refund(user, t["id"], amount=50)
        refund(user, t["id"])
        assert not {"Payment refunded", "Payment partially refunded", "Payment received"} & set(titles(user))

    def test_on_notifies_for_partial_and_full_refunds(self, client, user, providers):
        _, _, t = paid_invoice(user, client, 200)
        refund(user, t["id"], amount=50)
        refund(user, t["id"])
        assert {"Payment partially refunded", "Payment refunded"} <= set(titles(user))

    def test_other_notification_types_are_unaffected(self, client, user, providers):
        pn = _toggle(user, False)
        autos = {a["automation_type"]: a for a in user.get("/api/automations").json()}
        r = user.post(f"/api/automations/{autos['daily_revenue_summary']['id']}/run")
        assert r.status_code == 200 and "Daily revenue summary" in titles(user)

    def test_discrepancy_alerts_are_never_suppressed(self, client, user, providers):
        _toggle(user, False)
        inv = make_invoice(user, 100)
        order = make_link(user, inv)["provider_reference"]
        providers.capture_mode = "wrong_amount"
        pay(client, order)
        assert "Payment flagged" in titles(user)
        tamper_invoice(inv["id"], status="cancelled")  # an unmatched payment also still alerts
        inv2 = make_invoice(user, 100)
        o2 = make_link(user, inv2)["provider_reference"]
        providers.capture_mode = "completed"
        tamper_invoice(inv2["id"], total_amount=Decimal("999.00"))
        providers.orders[o2]["status"] = "COMPLETED"
        hook(client, {"id": "WH-D4", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": f"CAP-{o2}", "supplemental_data": {"related_ids": {"order_id": o2}}}})
        assert "Payment needs review" in titles(user)

    def test_the_switch_is_per_user(self, client, user, other, providers):
        _toggle(user, False)
        paid_invoice(user, client, 100)
        paid_invoice(other, client, 100)
        assert "Payment received" not in titles(user) and "Payment received" in titles(other)

    def test_a_user_cannot_flip_another_users_switch(self, user, other, providers):
        pn = next(a for a in user.get("/api/automations").json() if a["automation_type"] == "payment_notifications")
        assert other.patch(f"/api/automations/{pn['id']}", json={"is_enabled": False}).status_code == 404


# ================================================================== D5: per-user and per-IP rate limits
def client_from(app, ip):
    """A TestClient whose requests appear to come from `ip` (this Starlette version has no TestClient(client=...))."""
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "client": (ip, 50000)}
        await app(scope, receive, send)

    return TestClient(wrapped)


def _hit(auth, n, path="/api/payments/paypal/create-order"):
    return [auth.post(path, json={"invoice_id": str(uuid.uuid4())}).status_code for _ in range(n)]


class TestD5RateLimits:
    def test_original_scenario_one_user_cannot_consume_anothers_quota(self, client, providers):
        """Was: after user A used up the limit, user B's very first request was also 429 (limits were keyed by IP)."""
        a, b = Auth(client), Auth(client)
        codes_a = _hit(a, 22)
        assert codes_a.count(429) == 2 and 429 not in codes_a[:20]
        assert _hit(b, 1)[0] != 429  # B has a full, separate quota (404: the random invoice does not exist)
        assert _hit(a, 1)[0] == 429  # A is still limited

    def test_each_user_gets_the_full_quota_regardless_of_order(self, client, providers):
        a, b = Auth(client), Auth(client)
        assert 429 not in _hit(a, 20) and 429 not in _hit(b, 20)
        assert _hit(a, 1)[0] == 429 and _hit(b, 1)[0] == 429

    def test_the_limit_follows_the_user_not_the_token(self, client, providers):
        a = Auth(client)
        _hit(a, 20)
        from tests.conftest import make_token
        a2 = Auth(client)
        a2.id, a2.headers = a.id, {"Authorization": f"Bearer {make_token(a.id)}"}  # a fresh token for the same user
        assert _hit(a2, 1)[0] == 429

    def test_unauthenticated_requests_never_consume_anyone_quota(self, client, providers):
        codes = [client.post("/api/payments/paypal/create-order", json={}).status_code for _ in range(30)]
        assert set(codes) == {401}
        assert 429 not in _hit(Auth(client), 20)

    def test_different_limits_are_independent_per_user(self, client, providers):
        a = Auth(client)
        _hit(a, 21)
        assert a.post("/api/payments/paypal/capture-order", json={"order_id": "abc"}).status_code != 429  # separate bucket ("pay-capture")

    def test_ai_endpoints_are_limited_per_user_too(self, client, providers):
        a, b = Auth(client), Auth(client)
        codes = [a.post("/api/ai/chat", json={"message": "hi"}).status_code for _ in range(21)]
        assert codes[-1] == 429 and 429 not in codes[:20]
        assert b.post("/api/ai/chat", json={"message": "hi"}).status_code != 429

    def test_public_endpoints_are_limited_per_client_ip(self, client, providers):
        codes = [client.post("/api/public/pay/paypal/capture", json={"order_id": "nope"}).status_code for _ in range(22)]
        assert codes[-1] == 429 and 429 not in codes[:20]
        other_ip = client_from(client.app, "203.0.113.9")
        assert other_ip.post("/api/public/pay/paypal/capture", json={"order_id": "nope"}).status_code != 429

    def test_forwarded_headers_are_ignored_by_default(self, client, providers):
        """No trusted proxy configured: a client cannot dodge the limit by inventing X-Forwarded-For values."""
        codes = [client.post("/api/public/pay/paypal/capture", json={"order_id": "nope"}, headers={"X-Forwarded-For": f"198.51.100.{i}"}).status_code for i in range(22)]
        assert codes[-1] == 429

    def test_with_one_trusted_proxy_the_address_that_proxy_saw_is_used(self, client, providers, monkeypatch):
        from app.core.config import get_settings
        monkeypatch.setenv("TRUSTED_PROXY_COUNT", "1")
        get_settings.cache_clear()
        post = lambda xff: client.post("/api/public/pay/paypal/capture", json={"order_id": "nope"}, headers={"X-Forwarded-For": xff}).status_code
        # client-supplied leading entries differ, but the entry appended by the trusted proxy is the same -> same bucket
        codes = [post(f"spoof-{i}, 192.0.2.50") for i in range(22)]
        assert codes[-1] == 429
        assert post("spoof, 192.0.2.51") != 429  # a different real client address has its own quota

    def test_client_ip_resolution(self):
        from app.core.ratelimit import client_ip

        class Req:
            def __init__(self, peer, xff=None):
                self.client = type("C", (), {"host": peer})() if peer else None
                self.headers = {"x-forwarded-for": xff} if xff is not None else {}

        assert client_ip(Req("10.0.0.1", "1.2.3.4"), 0) == "10.0.0.1"  # header ignored when no proxy is trusted
        assert client_ip(Req("10.0.0.1", "evil, 1.2.3.4"), 1) == "1.2.3.4"  # rightmost entry = what the proxy saw
        assert client_ip(Req("10.0.0.1", "client, 1.2.3.4, 5.6.7.8"), 2) == "1.2.3.4"  # two trusted hops
        assert client_ip(Req("10.0.0.1"), 1) == "10.0.0.1"  # no header: use the peer
        assert client_ip(Req("10.0.0.1", "1.2.3.4"), 2) == "10.0.0.1"  # fewer hops than trusted proxies: do not trust it
        assert client_ip(Req("10.0.0.1", "not-an-ip"), 1) == "10.0.0.1"  # garbage falls back to the peer
        assert client_ip(Req("10.0.0.1", "2001:db8::1"), 1) == "2001:db8::1"
        assert client_ip(Req(None), 0) == "unknown"

    def test_trusted_proxy_count_is_validated(self, monkeypatch):
        from pydantic import ValidationError

        from app.core.config import Settings
        monkeypatch.setenv("TRUSTED_PROXY_COUNT", "-1")
        with pytest.raises(ValidationError):
            Settings()
        monkeypatch.setenv("TRUSTED_PROXY_COUNT", "0")
        assert Settings().trusted_proxy_count == 0


# ================================================================== migration / schema consistency (no PostgreSQL needed)
class TestMigrationConsistency:
    MIGRATION = os.path.join(os.path.dirname(__file__), "..", "..", "supabase", "migrations", "20260102000000_money_integrity.sql")

    def _sql(self):
        return open(self.MIGRATION, encoding="utf-8").read().lower()

    def test_refunds_table_columns_match_the_model(self):
        import re

        from app.models import Refund
        sql = self._sql()
        body = re.search(r"create table if not exists public\.refunds \((.*?)\n\);", sql, re.S).group(1)
        cols = {line.split()[0] for line in body.split("\n") if line.strip() and not line.strip().startswith(("constraint", "--"))}
        assert cols == {c.name for c in Refund.__table__.columns}

    def test_added_columns_exist_on_the_models(self):
        import re

        from app.models import Base
        sql = self._sql()
        for table, col in re.findall(r"alter table public\.(\w+) add column if not exists (\w+)", sql):
            assert col in Base.metadata.tables[table].columns, f"{table}.{col}"

    def test_status_constraints_accept_every_status_the_code_uses(self):
        sql = self._sql()
        for status in ("created", "pending", "completed", "partially_refunded", "refunded", "failed", "cancelled"):
            assert f"'{status}'" in sql

    def test_refunds_are_read_only_for_clients_and_protected_by_rls(self):
        sql = self._sql()
        assert "enable row level security" in sql and "for select to authenticated" in sql
        assert "for insert" not in sql and "for update" not in sql and "for all" not in sql

    def test_unique_constraints_match_the_model(self):
        from app.models import Refund
        sql = self._sql()
        names = {c.name for c in Refund.__table__.constraints if c.name}
        assert {"uq_refund_provider_id", "uq_refund_idempotency"} <= names
        assert "uq_refund_provider_id unique (provider, provider_refund_id)" in sql

    def test_dev_sqlite_upgrade_adds_new_columns_to_an_old_database(self):
        """An existing local paypilot.db created before this change must keep working (production uses the SQL migration)."""
        from sqlalchemy import create_engine, inspect, text

        from app.core.db import upgrade_sqlite_dev_schema
        from app.models import Base
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with engine.begin() as c:
            for stmt in ("ALTER TABLE invoices DROP COLUMN version", "ALTER TABLE transactions DROP COLUMN refunded_amount",
                         "ALTER TABLE transactions DROP COLUMN invoice_version", "ALTER TABLE payment_links DROP COLUMN invoice_version", "DROP TABLE refunds"):
                c.execute(text(stmt))
        added = upgrade_sqlite_dev_schema(engine)
        assert {"invoices.version", "transactions.refunded_amount", "transactions.invoice_version", "payment_links.invoice_version"} <= set(added)
        Base.metadata.create_all(engine)  # creates the missing refunds table, as the app does at startup
        assert "refunds" in inspect(engine).get_table_names()
        assert upgrade_sqlite_dev_schema(engine) == []  # idempotent


# ================================================================== row locks must be valid on PostgreSQL (SQLite cannot show this)
class TestRowLocksArePostgresSafe:
    """The models eager-load relationships with LEFT OUTER JOINs. PostgreSQL rejects `SELECT ... FOR UPDATE` that would lock the nullable
    side of an outer join, so every lock must say which table it locks (`FOR UPDATE OF <table>`). SQLite ignores row locks entirely, which
    is why this is checked by compiling for the PostgreSQL dialect and by scanning the source."""

    def _compile(self, stmt):
        from sqlalchemy.dialects import postgresql
        return str(stmt.compile(dialect=postgresql.dialect()))

    @pytest.mark.parametrize("model_name", ["Transaction", "Invoice", "PaymentLink"])
    def test_locking_statements_name_the_locked_table(self, model_name):
        import app.models as m
        model = getattr(m, model_name)
        bare = self._compile(select(model).with_for_update())
        assert "LEFT OUTER JOIN" in bare and bare.rstrip().endswith("FOR UPDATE")  # documents the hazard this guards against
        scoped = self._compile(select(model).with_for_update(of=model))
        assert f"FOR UPDATE OF {model.__tablename__}" in scoped

    def test_no_unscoped_for_update_remains_in_the_backend(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parents[1] / "app"
        offenders = []
        for f in root.rglob("*.py"):
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"with_for_update\(\s*\)", line) or re.search(r"with_for_update\s*=\s*True", line):
                    offenders.append(f"{f.relative_to(root)}:{n}")
        assert offenders == [], f"FOR UPDATE without OF (fails on PostgreSQL with outer-joined models): {offenders}"
