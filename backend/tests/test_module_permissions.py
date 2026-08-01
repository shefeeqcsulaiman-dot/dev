"""Superadmin's per-company "Module Permissions" (companies.modules_enabled)
were stored correctly on create/edit but never surfaced to the company's own
dashboard — the sidebar always showed every module regardless of what was
granted. Covers the bootstrap payload exposing modules_enabled and the two
null-safety cases: a brand-new self-registered company (explicit full list)
and a pre-existing company row with the column left NULL (must mean
"unrestricted", not "nothing enabled", or every company predating this
column would suddenly lose its whole sidebar)."""
from app.models import Company, User
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
