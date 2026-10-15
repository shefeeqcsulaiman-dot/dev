"""Super Admin > Errors: every 500 (a bug) and 503 (overloaded) the server answers is recorded
(app/server_errors.py) and listed at /superadmin/server-errors with the person and company."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app import server_errors
from app.main import app
from tests.test_ess_requests import _company_id, _ess_login
from tests.test_impersonate_branch import _make_superadmin_headers


@pytest.fixture
def raw_client(client):
    """Like `client`, but a crash comes back as the real 500/503 response instead of
    being re-raised into the test."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _break_records_endpoint(monkeypatch, exc):
    import app.routers.app_data as app_data

    def boom(*_a, **_k):
        raise exc
    monkeypatch.setattr(app_data, "resolve_principal_company", boom)


def test_a_500_is_recorded_with_who_and_trace(raw_client, client, db, auth_headers, monkeypatch):
    server_errors.clear()
    _break_records_endpoint(monkeypatch, RuntimeError("records exploded"))
    r = raw_client.get("/api/v1/app-data/records/products", headers=auth_headers)
    assert r.status_code == 500

    sa = _make_superadmin_headers(client, db)
    body = client.get("/api/v1/superadmin/server-errors", headers=sa).json()
    e = body["errors"][0]
    assert (e["kind"], e["status"], e["method"], e["path"], e["error"]) == (
        "error", 500, "GET", "/api/v1/app-data/records/products", "RuntimeError")
    assert "records exploded" in e["message"] and "records exploded" in e["trace"]
    assert e["who"] and e["company_name"]


def test_an_overload_503_is_recorded_for_an_employee_login(raw_client, client, db, auth_headers, monkeypatch):
    server_errors.clear()
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "SERR-1", "serr.one")
    _break_records_endpoint(monkeypatch, PoolTimeout("QueuePool limit of size 10 overflow 15 reached"))
    r = raw_client.get("/api/v1/app-data/records/products", headers=h)
    assert r.status_code == 503

    sa = _make_superadmin_headers(client, db)
    e = client.get("/api/v1/superadmin/server-errors", headers=sa).json()["errors"][0]
    assert (e["kind"], e["status"], e["trace"]) == ("overload", 503, None)
    assert "SERR-1" in e["who"]


def test_the_save_action_is_kept(raw_client, client, db, auth_headers, monkeypatch):
    server_errors.clear()
    import app.routers.app_data as app_data

    def boom(*_a, **_k):
        raise RuntimeError("save exploded")
    monkeypatch.setattr(app_data, "assert_collection_write_permission", boom)
    r = raw_client.post("/api/v1/app-data", params={"action": "save"}, headers=auth_headers,
                        json={"collection": "products", "record": {"code": "X"}})
    assert r.status_code == 500
    sa = _make_superadmin_headers(client, db)
    e = client.get("/api/v1/superadmin/server-errors", headers=sa).json()["errors"][0]
    assert (e["method"], e["action"]) == ("POST", "save")


def test_only_superadmin_can_read_or_clear(client, db, auth_headers):
    assert client.get("/api/v1/superadmin/server-errors", headers=auth_headers).status_code == 403
    assert client.delete("/api/v1/superadmin/server-errors", headers=auth_headers).status_code == 403
    sa = _make_superadmin_headers(client, db)
    assert client.delete("/api/v1/superadmin/server-errors", headers=sa).json()["ok"] is True
    assert client.get("/api/v1/superadmin/server-errors", headers=sa).json()["errors"] == []


def test_ordinary_4xx_are_not_recorded(client, db, auth_headers):
    server_errors.clear()
    client.get("/api/v1/app-data/records/products")                       # 401
    client.get("/api/v1/leave/requests/not-a-real-id", headers=auth_headers)  # 404/405
    sa = _make_superadmin_headers(client, db)
    assert client.get("/api/v1/superadmin/server-errors", headers=sa).json()["errors"] == []
