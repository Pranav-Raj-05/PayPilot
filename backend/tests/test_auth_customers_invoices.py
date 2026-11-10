from tests.conftest import make_token


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_requires_auth(client):
    assert client.get("/api/customers").status_code == 401
    r = client.get("/api/customers", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_rejects_wrong_secret_expired_and_audience(client):
    for tok in (make_token(secret="x" * 40), make_token(exp=-10), make_token(aud="anon")):
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_profile_created_from_verified_token(user):
    me = user.get("/api/auth/me").json()
    assert me["id"] == user.id and me["email"] == "alex@example.com" and me["full_name"] == "Alex Vance"
    upd = user.patch("/api/settings/profile", json={"business_name": "Vance Studio", "default_currency": "eur"}).json()
    assert upd["business_name"] == "Vance Studio" and upd["default_currency"] == "EUR"


def test_client_supplied_user_id_is_ignored(user, other):
    r = user.post("/api/customers", json={"name": "Mallory", "user_id": other.id})
    assert r.status_code == 201
    assert other.get("/api/customers").json()["total"] == 0
    assert user.get("/api/customers").json()["total"] == 1


def test_customer_create_validate_search(user):
    assert user.post("/api/customers", json={"name": ""}).status_code == 422
    assert user.post("/api/customers", json={"name": "A", "email": "not-an-email"}).status_code == 422
    user.post("/api/customers", json={"name": "Marcus Vance", "email": "m@studio.co"})
    user.post("/api/customers", json={"name": "Sarah Johnson"})
    assert user.get("/api/customers", params={"search": "vance"}).json()["total"] == 1


def test_invoice_totals_numbering_and_validation(user):
    body = {"description": "x", "currency": "USD", "tax_rate": 10,
            "items": [{"description": "a", "quantity": 2, "unit_price": "10.10"}, {"description": "b", "quantity": 1, "unit_price": 5}]}
    inv = user.post("/api/invoices", json=body).json()
    assert inv["subtotal"] == 25.2 and inv["tax"] == 2.52 and inv["total_amount"] == 27.72
    assert inv["status"] == "draft" and inv["invoice_number"].endswith("0001")
    assert user.post("/api/invoices", json=body).json()["invoice_number"].endswith("0002")
    for bad in ({**body, "items": []}, {**body, "currency": "XXX"}, {**body, "tax_rate": 101},
                {**body, "items": [{"description": "a", "quantity": 0, "unit_price": 1}]},
                {**body, "items": [{"description": "a", "quantity": 1, "unit_price": -1}]}):
        assert user.post("/api/invoices", json=bad).status_code == 422
    zero = {**body, "tax_rate": 0, "items": [{"description": "free", "quantity": 1, "unit_price": 0}]}
    assert user.post("/api/invoices", json=zero).status_code == 400


def test_invoice_edit_and_delete_rules(user, invoice):
    r = user.patch(f"/api/invoices/{invoice['id']}", json={"items": [{"description": "New", "quantity": 3, "unit_price": 100}], "tax_rate": 5})
    assert r.json()["total_amount"] == 315.0
    user.patch(f"/api/invoices/{invoice['id']}", json={"status": "pending"})
    assert user.patch(f"/api/invoices/{invoice['id']}", json={"description": "nope"}).status_code == 409
    assert user.delete(f"/api/invoices/{invoice['id']}").status_code == 409  # only drafts deletable
    draft = user.post("/api/invoices", json={"items": [{"description": "d", "unit_price": 1}]}).json()
    assert user.delete(f"/api/invoices/{draft['id']}").status_code == 204


def test_invoice_cannot_use_another_users_customer(user, other):
    cust = other.post("/api/customers", json={"name": "Theirs"}).json()
    r = user.post("/api/invoices", json={"customer_id": cust["id"], "items": [{"description": "d", "unit_price": 1}]})
    assert r.status_code == 404


def test_ownership_isolated_across_resources(user, other, invoice):
    assert other.get(f"/api/invoices/{invoice['id']}").status_code == 404
    assert other.patch(f"/api/invoices/{invoice['id']}", json={"status": "cancelled"}).status_code == 404
    assert other.delete(f"/api/invoices/{invoice['id']}").status_code == 404
    assert other.get("/api/invoices").json()["total"] == 0
    assert other.post(f"/api/invoices/{invoice['id']}/payment-link").status_code == 404
