"""Rate limits are keyed per signed-in login, not per (shared) IP."""
from starlette.requests import Request

from app.limiter import rate_limit_key
from app.security import create_access_token


def _request(headers: dict[str, str], client_ip: str = "203.0.113.7") -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_ip, 1234),
    })


def test_two_logins_behind_one_office_ip_get_separate_buckets():
    a = rate_limit_key(_request({"Authorization": f"Bearer {create_access_token('user-a')}"}))
    b = rate_limit_key(_request({"Authorization": f"Bearer {create_access_token('user-b')}"}))
    assert a == "user:user-a" and b == "user:user-b"


def test_employee_tokens_are_keyed_by_employee():
    assert rate_limit_key(_request({"Authorization": f"Bearer {create_access_token('emp:E-1')}"})) == "user:emp:E-1"


def test_no_or_forged_token_falls_back_to_the_ip():
    assert rate_limit_key(_request({})) == "203.0.113.7"
    forged = create_access_token("user-a")[:-4] + "AAAA"
    assert rate_limit_key(_request({"Authorization": f"Bearer {forged}"})) == "203.0.113.7"
    assert rate_limit_key(_request({"Authorization": "Bearer not-a-jwt"})) == "203.0.113.7"
    assert rate_limit_key(_request({"X-Forwarded-For": "1.1.1.1, 198.51.100.9"})) == "198.51.100.9"
