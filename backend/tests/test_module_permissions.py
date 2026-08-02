"""Superadmin's per-company "Module Permissions" (companies.modules_enabled)
were stored correctly on create/edit but never surfaced to the company's own
dashboard — the sidebar always showed every module regardless of what was
granted. Covers the bootstrap payload exposing modules_enabled and the two
null-safety cases: a brand-new self-registered company (explicit full list)
and a pre-existing company row with the column left NULL (must mean
"unrestricted", not "nothing enabled", or every company predating this
column would suddenly lose its whole sidebar).

Also covers server-side enforcement (require_module / company_allows_module
in app/dependencies.py) — the sidebar hide was previously the ONLY gate, so a
disabled module's data was still fully reachable via a direct API call. These
tests hit the API directly (bypassing the frontend nav) to prove the 403
actually happens at the backend, not just that the UI hides a button."""
from app.models import Company, Employee, User
from app.security import hash_password


def _make_superadmin(client, db, tag):
    company = Company(name="ETaxFlow Admin Test", trn=f"MODPERM-SUPERADMIN-{tag}")
    db.add(company)
    db.flush()
    admin = User(company_id=company.id, email=f"modperm-superadmin-{tag}@etaxflow.com",
                 full_name="Super Admin", role="superadmin", password_hash=hash_password("test12345"))
    db.add(admin)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_bootstrap_exposes_restricted_modules_enabled(client, db):
    sa_headers = _make_superadmin(client, db, "a")
    resp = client.post("/api/v1/superadmin/companies", json={
        "name": "Restricted Co",
        "email": "restricted-admin@example.com",
        "password": "admin12345",
        "full_name": "Restricted Admin",
        "modules": ["sales", "reports"],
    }, headers=sa_headers)
    assert resp.status_code in (200, 201), resp.text

    login = client.post("/api/v1/auth/login", json={"email": "restricted-admin@example.com", "password": "admin12345"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    boot = client.get("/api/v1/app-data", headers=headers)
    assert boot.status_code == 200, boot.text
    company = boot.json()["data"]["company"]
    assert company["modules_enabled"] == ["sales", "reports"]


def test_bootstrap_treats_null_modules_enabled_as_unrestricted(client, db):
    """A company row that predates this column (or was never explicitly
    restricted) must show every module — not be silently locked out."""
    sa_headers = _make_superadmin(client, db, "b")
    resp = client.post("/api/v1/superadmin/companies", json={
        "name": "Legacy Co",
        "email": "legacy-admin@example.com",
        "password": "admin12345",
        "full_name": "Legacy Admin",
    }, headers=sa_headers)
    assert resp.status_code in (200, 201), resp.text

    company = db.query(Company).filter(Company.name == "Legacy Co").first()
    company.modules_enabled = None
    db.commit()

    login = client.post("/api/v1/auth/login", json={"email": "legacy-admin@example.com", "password": "admin12345"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    boot = client.get("/api/v1/app-data", headers=headers)
    assert boot.status_code == 200, boot.text
    assert boot.json()["data"]["company"]["modules_enabled"] is None


def _make_restricted_company(client, db, tag, modules):
    sa_headers = _make_superadmin(client, db, tag)
    resp = client.post("/api/v1/superadmin/companies", json={
        "name": f"Restricted Co {tag}",
        "email": f"restricted-{tag}@example.com",
        "password": "admin12345",
        "full_name": "Restricted Admin",
        "modules": modules,
    }, headers=sa_headers)
    assert resp.status_code in (200, 201), resp.text
    company_id = resp.json()["company_id"]
    login = client.post("/api/v1/auth/login", json={"email": f"restricted-{tag}@example.com", "password": "admin12345"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return company_id, headers, sa_headers


def test_disabled_module_blocks_direct_api_call(client, db):
    """The sidebar hides the Inventory nav item when "inventory" isn't
    granted, but that's UI-only — hitting the inventory API directly used to
    work regardless. require_module() on inventory.py's router must 403 it."""
    _, headers, _ = _make_restricted_company(client, db, "api-block", ["sales"])
    r = client.get("/api/v1/warehouses", headers=headers)
    assert r.status_code == 403, r.text
    # sales IS granted — same company, a sales-module endpoint must still work.
    r2 = client.get("/api/v1/invoices", headers=headers)
    assert r2.status_code == 200, r2.text


def test_unrestricted_company_not_affected_by_module_gate(client, auth_headers):
    """auth_headers' tenant has modules_enabled = NULL (unrestricted) — the
    new require_module() gate must be a no-op for it, same as every other
    existing test in this suite that hits inventory/accounting/etc."""
    r = client.get("/api/v1/warehouses", headers=auth_headers)
    assert r.status_code == 200, r.text


def test_disabled_module_blocks_appdata_collection_save(client, db):
    """app_data.py's generic /app-data?action=save is the single choke point
    almost every module's own records flow through (see _COLLECTION_MODULE in
    app_data.py) — must be gated too, not just the dedicated routers."""
    _, headers, _ = _make_restricted_company(client, db, "appdata-block", ["sales"])
    r = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "bills", "record": {"ref": "BILL-1", "amount": 100}},
    )
    assert r.status_code == 403, r.text
    # salesInvoices IS "sales", which is granted — must still be saveable.
    r2 = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "salesInvoices", "record": {"invoice_no": "INV-1", "amount": 100}},
    )
    assert r2.status_code == 200, r2.text


def test_ess_login_blocked_when_ess_module_disabled(client, db):
    company_id, _, sa_headers = _make_restricted_company(client, db, "ess-block", ["sales", "hrms"])
    emp = Employee(company_id=company_id, employee_no="ESS-BLOCK-001", full_name="ESS Block Test")
    db.add(emp)
    db.commit()

    r = client.post("/api/v1/ess/login", json={"username": "ESS-BLOCK-001", "company_id": company_id, "password": "ESS-BLOCK-001"})
    assert r.status_code == 403, r.text

    r2 = client.put(
        f"/api/v1/superadmin/companies/{company_id}/modules",
        headers=sa_headers,
        json={"modules": ["sales", "hrms", "ess"]},
    )
    assert r2.status_code == 200, r2.text
    r3 = client.post("/api/v1/ess/login", json={"username": "ESS-BLOCK-001", "company_id": company_id, "password": "ESS-BLOCK-001"})
    assert r3.status_code == 200, r3.text
