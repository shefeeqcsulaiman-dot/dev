from app.models import Invoice


def test_auth_rejects_missing_token(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_auth_login_and_me(client, auth_headers):
    response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "qa-admin@taxflowqa.com"


def test_tenant_isolation_for_invoices(client, db, auth_headers, second_tenant_headers):
    created = client.post(
        "/api/v1/invoices",
        headers=auth_headers,
        json={
            "customer_name": "Tenant One Customer",
            "invoice_number": "TENANT-ONLY-001",
            "lines": [{"description": "Service", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201

    own_rows = client.get("/api/v1/invoices", headers=auth_headers)
    other_rows = client.get("/api/v1/invoices", headers=second_tenant_headers)

    assert any(row["invoice_number"] == "TENANT-ONLY-001" for row in own_rows.json())
    assert all(row["invoice_number"] != "TENANT-ONLY-001" for row in other_rows.json())
    assert db.query(Invoice).filter(Invoice.invoice_number == "TENANT-ONLY-001").count() == 1
