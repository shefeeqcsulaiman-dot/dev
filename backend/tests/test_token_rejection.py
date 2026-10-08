"""Bad tokens are refused with 401, never accepted or turned into a 500: tampered,
expired, signed with another key, unsigned (alg "none"), or not a JWT at all."""
import base64
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config import get_settings


def _b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def _good_token(client, auth_headers):
    return auth_headers["Authorization"].split(" ", 1)[1]


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/auth/whoami", "/api/v1/app-data"])
def test_bad_tokens_get_401(client, auth_headers, path):
    good = _good_token(client, auth_headers)
    sub = jwt.decode(good, get_settings().secret_key, algorithms=["HS256"])["sub"]
    header, payload, signature = good.split(".")
    tampered = f"{header}.{_b64({'sub': sub, 'exp': 9999999999})}.{signature}"
    expired = jwt.encode({"sub": sub, "exp": datetime.now(UTC) - timedelta(minutes=1)}, get_settings().secret_key, algorithm="HS256")
    other_key = jwt.encode({"sub": sub, "exp": datetime.now(UTC) + timedelta(hours=1)}, "not-the-server-secret-0123456789abcdef", algorithm="HS256")
    unsigned = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64({'sub': sub, 'exp': 9999999999})}."
    for token in (tampered, expired, other_key, unsigned, "not-a-jwt", "a.b.c"):
        r = client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, (path, token[:20], r.status_code, r.text[:200])
    assert client.get(path, headers=auth_headers).status_code == 200


def test_ess_portal_rejects_bad_tokens(client):
    expired = jwt.encode({"sub": "employee:x", "exp": datetime.now(UTC) - timedelta(minutes=1)}, get_settings().secret_key, algorithm="HS256")
    for token in (expired, "not-a-jwt"):
        r = client.get("/api/v1/ess/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (401, 403), (r.status_code, r.text[:200])
