"""Company suspend/expiry enforcement — Company.subscription_expires_at
existed and superadmin could set it via POST /superadmin/companies/{id}/set-
expiry, but nothing anywhere actually checked it: a "suspended" company had
zero real effect. Reuses that same field as the suspend control (a past/
today date = suspended, blank/future = active) rather than adding a
separate flag. Enforced at every point auth actually resolves through —
all three login endpoints (auth/login, hr/login, ess/login) plus every
subsequent-request auth path (get_current_user, _principal_from_user_token,
_principal_from_employee_token, get_current_employee, ESS's token path) —
so neither a fresh login nor an already-open session can bypass it."""
from datetime import UTC, datetime, timedelta

from app.models import Company, Employee, User
from app.security import hash_password
from tests.conftest import seed_accounts


def _make_company_admin(db, trn_suffix):
    company = Company(name=f"Suspend Test Co {trn_suffix}", trn=f"SUSPEND-TEST-{trn_suffix}", country="United Arab Emirates")
    db.add(company)
    db.flush()
    user = User(
        company_id=company.id, email=f"suspend-admin-{trn_suffix}@taxflowqa.com".lower(),
        full_name="Suspend Test Admin", role="admin", password_hash=hash_password("admin123"),
    )
    db.add(user)
    seed_accounts(db, company.id)
    db.commit()
    return company, user


def _make_employee_with_portal_access(db, company_id, suffix, username):
    emp = Employee(
        company_id=company_id, employee_no=f"SUSP-EMP-{suffix}", full_name="Suspend Test Employee",
        username=username, password_hash=hash_password("emptest123"), is_active=True,
    )
    db.add(emp)
    db.commit()
    return emp


def _yesterday():
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")


def _tomorrow():
    return (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")


def test_null_expiry_never_blocks_admin_login(client, db):
    """Backward compatibility: every existing company has NULL
    subscription_expires_at today — this must stay a complete no-op."""
    company, user = _make_company_admin(db, "NULL1")
    assert company.subscription_expires_at is None
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert r.status_code == 200, r.text


def test_expired_company_blocks_admin_login(client, db):
    company, user = _make_company_admin(db, "ADMIN1")
    company.subscription_expires_at = _yesterday()
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert r.status_code == 403, r.text
    assert "subscription" in r.json()["detail"].lower()


def test_today_expiry_blocks_login_immediately(client, db):
    """Regression test: superadmin's "Suspend Now" quick action sets the
    expiry to TODAY's date, expecting immediate effect — a strict `<`
    comparison would let it through until midnight, silently defeating the
    button's entire purpose. Caught via live browser verification."""
    company, user = _make_company_admin(db, "TODAY1")
    company.subscription_expires_at = datetime.now(UTC).strftime("%Y-%m-%d")
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert r.status_code == 403, r.text


def test_future_expiry_does_not_block_login(client, db):
    company, user = _make_company_admin(db, "FUTURE1")
    company.subscription_expires_at = _tomorrow()
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert r.status_code == 200, r.text


def test_expired_company_blocks_already_authenticated_admin_request(client, db):
    """An admin logged in BEFORE the company was suspended must be blocked
    on their very next request, not just on a fresh login attempt."""
    company, user = _make_company_admin(db, "ADMIN2")
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    still_ok = client.get("/api/v1/auth/me", headers=headers)
    assert still_ok.status_code == 200

    company.subscription_expires_at = _yesterday()
    db.commit()

    blocked = client.get("/api/v1/auth/me", headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert "subscription" in blocked.json()["detail"].lower()


def test_clearing_expiry_restores_access(client, db):
    company, user = _make_company_admin(db, "RESTORE1")
    company.subscription_expires_at = _yesterday()
    db.commit()
    blocked = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert blocked.status_code == 403

    company.subscription_expires_at = None
    db.commit()
    restored = client.post("/api/v1/auth/login", json={"email": user.email, "password": "admin123"})
    assert restored.status_code == 200, restored.text


def test_expired_company_blocks_hr_login(client, db):
    company, _ = _make_company_admin(db, "HR1")
    emp = _make_employee_with_portal_access(db, company.id, "HR1", "suspend.hr1")
    company.subscription_expires_at = _yesterday()
    db.commit()
    r = client.post("/api/v1/hr/login", json={"username": "suspend.hr1", "password": "emptest123"})
    assert r.status_code == 403, r.text
    assert "subscription" in r.json()["detail"].lower()


def test_expired_company_blocks_ess_login(client, db):
    company, _ = _make_company_admin(db, "ESS1")
    _make_employee_with_portal_access(db, company.id, "ESS1", "suspend.ess1")
    company.subscription_expires_at = _yesterday()
    db.commit()
    r = client.post("/api/v1/ess/login", json={"username": "suspend.ess1", "password": "emptest123"})
    assert r.status_code == 403, r.text
    assert "subscription" in r.json()["detail"].lower()


def test_expired_company_blocks_already_authenticated_employee_request(client, db):
    company, _ = _make_company_admin(db, "EMP2")
    emp = _make_employee_with_portal_access(db, company.id, "EMP2", "suspend.emp2")
    r = client.post("/api/v1/hr/login", json={"username": "suspend.emp2", "password": "emptest123"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    still_ok = client.get("/api/v1/hr/me", headers=headers)
    assert still_ok.status_code == 200

    company.subscription_expires_at = _yesterday()
    db.commit()

    blocked = client.get("/api/v1/hr/me", headers=headers)
    assert blocked.status_code == 403, blocked.text
    assert "subscription" in blocked.json()["detail"].lower()


def test_expired_company_does_not_block_superadmin(client, db):
    """Superadmin's own sentinel company (SUPERADMIN-INTERNAL) never has an
    expiry set — confirms it stays NULL/unrestricted even while other
    companies on the platform are suspended, with no special-case code."""
    other_company, _ = _make_company_admin(db, "UNRELATED1")
    other_company.subscription_expires_at = _yesterday()
    db.commit()

    sa_company = db.query(Company).filter(Company.trn == "SUPERADMIN-INTERNAL").first()
    if not sa_company:
        sa_company = Company(name="ETaxFlow Admin", trn="SUPERADMIN-INTERNAL")
        db.add(sa_company)
        db.flush()
    sa_user = db.query(User).filter(User.email == "suspend-test-superadmin@etaxflow.com").first()
    if not sa_user:
        sa_user = User(
            company_id=sa_company.id, email="suspend-test-superadmin@etaxflow.com",
            full_name="Suspend Test Superadmin", role="superadmin", password_hash=hash_password("test12345"),
        )
        db.add(sa_user)
    db.commit()
    assert sa_company.subscription_expires_at is None

    r = client.post("/api/v1/auth/login", json={"email": sa_user.email, "password": "test12345"})
    assert r.status_code == 200, r.text
