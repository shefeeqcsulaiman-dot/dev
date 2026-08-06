"""POST /ess/login and POST /hr/login had no per-route rate limit (unlike
/auth/login's 10/minute), leaving the employee/HR portal logins wide open to
credential stuffing. Also covers the X-Forwarded-For trust bug in
app/limiter.py: the rate limiter used to key off the FIRST value in that
header, which is fully client-controlled — a client could send a fresh fake
IP on every request and bypass every IP-keyed rate limit, including
/auth/login's own.

Also covers a follow-up app-wide security pass: three superadmin endpoints
(reset-password, delete-company, impersonate) had no rate limit at all —
each requires a valid superadmin token already, so this isn't a pre-auth
brute-force vector, but a compromised/leaked superadmin token (or a buggy
script) could otherwise hammer them without any throttling."""
from starlette.requests import Request

from app.config import get_settings
from app.limiter import _get_client_ip, limiter
from app.models import Company, Employee, User
from app.security import hash_password


def test_get_client_ip_trusts_last_forwarded_hop_not_first():
    """The first X-Forwarded-For entry is whatever the client itself sent
    (spoofable); the last is the nearest hop, set by DigitalOcean's own
    proxy — that's the one to trust."""
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"9.9.9.9, 203.0.113.7")],
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert _get_client_ip(request) == "203.0.113.7"


def test_get_client_ip_falls_back_to_remote_address_without_headers():
    scope = {"type": "http", "headers": [], "client": ("198.51.100.1", 12345)}
    request = Request(scope)
    assert _get_client_ip(request) == "198.51.100.1"


def _toggle_limiter_enabled(value: bool) -> bool:
    previous = limiter.enabled
    limiter.enabled = value
    return previous


def test_ess_login_rate_limited(client, db):
    previous = _toggle_limiter_enabled(True)
    try:
        emp = Employee(company_id="rate-limit-test-co", employee_no="RATE-001", full_name="Rate Limit Test")
        db.add(emp)
        db.commit()
        last_status = None
        for _ in range(11):
            r = client.post("/api/v1/ess/login", json={"username": "RATE-001", "company_id": "rate-limit-test-co", "password": "wrong"})
            last_status = r.status_code
        assert last_status == 429, f"expected 429 after 11 rapid attempts, got {last_status}"
    finally:
        limiter.enabled = previous


def test_hr_login_rate_limited(client, db):
    previous = _toggle_limiter_enabled(True)
    try:
        emp = Employee(company_id="rate-limit-test-co-2", employee_no="RATE-002", full_name="Rate Limit Test 2")
        db.add(emp)
        db.commit()
        last_status = None
        for _ in range(11):
            r = client.post("/api/v1/hr/login", json={"username": "RATE-002", "company_id": "rate-limit-test-co-2", "password": "wrong"})
            last_status = r.status_code
        assert last_status == 429, f"expected 429 after 11 rapid attempts, got {last_status}"
    finally:
        limiter.enabled = previous


def _make_superadmin_headers(client, db):
    admin = db.query(User).filter(User.email == "sec-hardening-superadmin@etaxflow.com").first()
    if not admin:
        company = Company(name="Sec Hardening Admin Test", trn="SEC-HARDENING-SUPERADMIN")
        db.add(company)
        db.flush()
        admin = User(company_id=company.id, email="sec-hardening-superadmin@etaxflow.com",
                     full_name="Sec Hardening Super Admin", role="superadmin",
                     password_hash=hash_password("test12345"))
        db.add(admin)
        db.commit()
    r = client.post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_superadmin_reset_password_rate_limited(client, db):
    previous = _toggle_limiter_enabled(True)
    try:
        headers = _make_superadmin_headers(client, db)
        last_status = None
        for _ in range(21):
            r = client.post(
                "/api/v1/superadmin/companies/nonexistent-company-id/reset-password",
                headers=headers, json={"user_id": "nonexistent-user-id", "password": "irrelevant"},
            )
            last_status = r.status_code
        assert last_status == 429, f"expected 429 after 21 rapid attempts, got {last_status}"
    finally:
        limiter.enabled = previous


def test_superadmin_impersonate_rate_limited(client, db):
    previous = _toggle_limiter_enabled(True)
    try:
        headers = _make_superadmin_headers(client, db)
        last_status = None
        for _ in range(21):
            r = client.post(
                "/api/v1/superadmin/companies/nonexistent-company-id/impersonate",
                headers=headers,
            )
            last_status = r.status_code
        assert last_status == 429, f"expected 429 after 21 rapid attempts, got {last_status}"
    finally:
        limiter.enabled = previous


def test_cors_wildcard_blocked_in_production():
    """CORS_ORIGINS="*" combined with allow_credentials=True lets Starlette's
    CORSMiddleware reflect any Origin header, effectively allowing any site to
    make credentialed cross-origin requests. Must be blocked at production
    startup, same as an insecure SECRET_KEY/ADMIN_PASSWORD already is."""
    settings = get_settings()
    original_env, original_origins = settings.app_env, settings.cors_origins
    try:
        settings.app_env = "production"
        settings.secret_key = "a-real-production-secret"
        settings.admin_password = "a-real-admin-password"
        settings.superadmin_password = "a-real-superadmin-password"
        settings.cors_origins = "*"
        try:
            settings.assert_production_secrets()
            assert False, "expected RuntimeError for wildcard CORS_ORIGINS in production"
        except RuntimeError as e:
            assert "CORS_ORIGINS" in str(e)
        settings.cors_origins = "https://app.etaxflow.com"
        settings.assert_production_secrets()  # should not raise
    finally:
        settings.app_env = original_env
        settings.cors_origins = original_origins
