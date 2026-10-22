"""ESS refresh tokens for the mobile app (POST /ess/login remember_device, /ess/refresh,
/ess/logout): the app stays signed in past the 24h access token so automatic GPS check-in
keeps working, and loses that sign-in whenever the employee's access or password changes."""
import uuid
from datetime import UTC, datetime, timedelta

from app.models import Company, EssRefreshToken
from tests.test_ess_requests import _company_id, _ess_login


def _uid():
    return uuid.uuid4().hex[:6]


def _remembered(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    username = f"rt.{_uid()}"
    emp, _ = _ess_login(client, db, auth_headers, cid, f"RT-{_uid()}", username)
    r = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123",
                                                "remember_device": True, "device": "Android 15; Pixel 9"})
    assert r.status_code == 200, r.text
    return emp, username, r.json()


def _refresh(client, token):
    return client.post("/api/v1/ess/refresh", json={"refresh_token": token})


def test_web_login_gets_no_refresh_token(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    username = f"web.{_uid()}"
    _ess_login(client, db, auth_headers, cid, f"WEB-{_uid()}", username)
    r = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    assert r.status_code == 200
    assert r.json()["refresh_token"] is None


def test_refresh_gives_a_working_access_token_and_only_stores_a_hash(client, db, auth_headers):
    emp, _, body = _remembered(client, db, auth_headers)
    token = body["refresh_token"]
    assert token
    row = db.query(EssRefreshToken).filter(EssRefreshToken.employee_id == emp.id).one()
    assert row.token_hash != token and len(row.token_hash) == 64
    assert row.device == "Android 15; Pixel 9"

    r = _refresh(client, token)
    assert r.status_code == 200, r.text
    assert r.json()["refresh_token"] == token
    me = client.get("/api/v1/ess/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["id"] == emp.id


def test_unknown_or_garbage_token_is_refused(client):
    assert _refresh(client, "not-a-real-token").status_code == 401


def test_logout_revokes_the_token(client, db, auth_headers):
    _, _, body = _remembered(client, db, auth_headers)
    assert client.post("/api/v1/ess/logout", json={"refresh_token": body["refresh_token"]}).status_code == 200
    assert _refresh(client, body["refresh_token"]).status_code == 401


def test_hr_password_reset_signs_remembered_devices_out(client, db, auth_headers):
    emp, _, body = _remembered(client, db, auth_headers)
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers,
                   json={"password": "brand-new-pw"})
    assert r.status_code == 200, r.text
    assert _refresh(client, body["refresh_token"]).status_code == 401


def test_own_password_change_keeps_this_device_and_signs_out_the_others(client, db, auth_headers):
    emp, username, this = _remembered(client, db, auth_headers)
    other = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123",
                                                    "remember_device": True}).json()
    h = {"Authorization": f"Bearer {this['access_token']}"}
    r = client.post("/api/v1/ess/change-password", headers=h, json={
        "current_password": f"{username}pw123", "new_password": "changed-pw-1", "refresh_token": this["refresh_token"],
    })
    assert r.status_code == 200, r.text
    replacement = r.json()["refresh_token"]
    assert replacement and replacement != this["refresh_token"]
    assert _refresh(client, replacement).status_code == 200
    assert _refresh(client, this["refresh_token"]).status_code == 401
    assert _refresh(client, other["refresh_token"]).status_code == 401


def test_revoked_portal_access_and_deactivation_stop_refresh(client, db, auth_headers):
    emp, _, body = _remembered(client, db, auth_headers)
    emp.status = "inactive"
    db.commit()
    assert _refresh(client, body["refresh_token"]).status_code == 403
    emp.status = "active"
    db.commit()
    assert _refresh(client, body["refresh_token"]).status_code == 200

    r = client.delete(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert _refresh(client, body["refresh_token"]).status_code == 401


def test_token_unused_for_90_days_expires(client, db, auth_headers):
    emp, _, body = _remembered(client, db, auth_headers)
    row = db.query(EssRefreshToken).filter(EssRefreshToken.employee_id == emp.id).one()
    row.last_used_at = datetime.now(UTC) - timedelta(days=91)
    db.commit()
    assert _refresh(client, body["refresh_token"]).status_code == 401


def test_ess_switched_off_for_the_company_stops_refresh(client, db, auth_headers):
    emp, _, body = _remembered(client, db, auth_headers)
    company = db.get(Company, emp.company_id)
    before = company.modules_enabled
    company.modules_enabled = '["accounting"]'
    db.commit()
    try:
        assert _refresh(client, body["refresh_token"]).status_code == 403
    finally:
        company.modules_enabled = before
        db.commit()
