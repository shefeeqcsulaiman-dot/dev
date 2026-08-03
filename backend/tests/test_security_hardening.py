"""POST /ess/login and POST /hr/login had no per-route rate limit (unlike
/auth/login's 10/minute), leaving the employee/HR portal logins wide open to
credential stuffing. Also covers the X-Forwarded-For trust bug in
app/limiter.py: the rate limiter used to key off the FIRST value in that
header, which is fully client-controlled — a client could send a fresh fake
IP on every request and bypass every IP-keyed rate limit, including
/auth/login's own."""
from starlette.requests import Request

from app.limiter import _get_client_ip, limiter
from app.models import Employee


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
