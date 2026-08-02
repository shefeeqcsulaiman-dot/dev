"""GET /reports/dashboard's _build_dashboard() used to call
app_sales_invoice_records() four times per request (once directly, plus once
each inside invoice_status/monthly_revenue_vat/top_customers, which all
independently recomputed the exact same query+JSON-parse). Fixed by
computing it once and threading it through as a parameter. These tests
prove that refactor didn't silently drop either data source (a real ORM
Invoice row vs an app-data "salesInvoices" record) from any of the three
downstream sections, and that tenant isolation still holds when passing the
list by reference instead of recomputing it per company."""
from tests.conftest import ensure_user


def _seed_dashboard_data(client, db, email, trn, db_customer, app_customer, db_invoice_no, app_invoice_no):
    admin = ensure_user(db, email, trn)
    db.commit()
    login = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # A real ORM Invoice row — created as "draft" by default, bumped to
    # "issued" so it counts toward revenue/status/monthly/top-customers
    # (all of which exclude drafts).
    inv = client.post(
        "/api/v1/invoices",
        headers=headers,
        json={
            "customer_name": db_customer,
            "invoice_number": db_invoice_no,
            "lines": [{"description": "DB line", "quantity": "1", "unit_price": "1000.00", "vat_rate": "5"}],
        },
    )
    assert inv.status_code == 201, inv.text
    from app.models import Invoice
    row = db.query(Invoice).filter(Invoice.company_id == admin.company_id, Invoice.invoice_number == db_invoice_no).first()
    row.status = "issued"
    db.commit()

    # An app-data "salesInvoices" record — the second, independent source
    # app_sales_invoice_records() merges in (dedup'd against Invoice.invoice_number).
    save = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={
            "collection": "salesInvoices",
            "record": {
                "invoice_no": app_invoice_no,
                "customer": app_customer,
                "status": "issued",
                "subtotal": "500.00",
                "total": "525.00",
                "vat_amount": "25.00",
                "date": "2026-06-15",
            },
        },
    )
    assert save.status_code == 200, save.text
    return headers


def test_dashboard_reflects_both_orm_and_appdata_invoices(client, db):
    headers = _seed_dashboard_data(
        client, db, "dash-both@taxflowqa.com", "900000000000101",
        "DB Dashboard Customer", "AppData Dashboard Customer",
        "DASH-DB-001", "DASH-APP-001",
    )

    r = client.get("/api/v1/reports/dashboard", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()

    # kpis.revenue = sum(subtotal) across both sources for non-draft invoices.
    assert float(data["kpis"]["revenue"]) >= 1500.00  # 1000 (DB) + 500 (app-data)

    # invoice_status: both records are "issued" -> pending bucket, and both
    # count toward the non-draft total.
    assert data["invoice_status"]["total"]["count"] >= 2

    # monthly_revenue_vat: at least one period must reflect the combined sales.
    monthly_sales = sum(float(p["sales"]) for p in data["monthly_revenue_vat"])
    assert monthly_sales >= 1500.00

    # top_customers: both customer names must appear — proves neither source
    # was dropped by the refactor.
    customer_names = {c["name"] for c in data["top_customers"]}
    assert "DB Dashboard Customer" in customer_names
    assert "AppData Dashboard Customer" in customer_names


def test_dashboard_tenant_isolation_after_refactor(client, db):
    headers_a = _seed_dashboard_data(
        client, db, "dash-iso-a@taxflowqa.com", "900000000000102",
        "Tenant A DB Customer", "Tenant A AppData Customer",
        "DASH-ISO-A-DB", "DASH-ISO-A-APP",
    )
    headers_b = _seed_dashboard_data(
        client, db, "dash-iso-b@taxflowqa.com", "900000000000103",
        "Tenant B DB Customer", "Tenant B AppData Customer",
        "DASH-ISO-B-DB", "DASH-ISO-B-APP",
    )

    dash_a = client.get("/api/v1/reports/dashboard", headers=headers_a).json()
    dash_b = client.get("/api/v1/reports/dashboard", headers=headers_b).json()

    names_a = {c["name"] for c in dash_a["top_customers"]}
    names_b = {c["name"] for c in dash_b["top_customers"]}
    assert "Tenant B DB Customer" not in names_a
    assert "Tenant B AppData Customer" not in names_a
    assert "Tenant A DB Customer" not in names_b
    assert "Tenant A AppData Customer" not in names_b
