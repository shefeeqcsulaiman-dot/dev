"""Fake GPS (the ESS app reports Android mock locations) and HR's Signed-in devices.

- A check-in from a mock location is refused, kept, and listed at /hr/gps-alerts.
- A mock ping while checked in counts as outside the site.
- /hr/gps-alerts follows the HR user's department scope.
- HR lists an employee's ESS app sign-ins and signs one or all out; signed-out phones can't refresh.
- The nightly cleanup deletes refresh tokens that can never work again."""
import uuid
from datetime import UTC, datetime, timedelta

from app.models import EmployeeLocationLog, EssRefreshToken
from app.worker import delete_dead_refresh_tokens
from tests.test_gps_checkin import CLINIC, NEAR_CLINIC, _checkin, _employee_with_sites


def _uid():
    return uuid.uuid4().hex[:6]


# ── fake GPS ─────────────────────────────────────────────────────────────────

def test_mock_location_check_in_is_refused_and_logged(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    r = client.post("/api/v1/hr/check-in", headers=h, json={
        "latitude": NEAR_CLINIC[0], "longitude": NEAR_CLINIC[1], "accuracy": 5, "is_mock": True, "device": "Pixel 9",
    })
    assert r.status_code == 403 and "fake location" in r.json()["detail"]
    assert client.get("/api/v1/hr/dashboard", headers=h).json()["checked_in"] is False
    log = db.query(EmployeeLocationLog).filter(EmployeeLocationLog.employee_id == emp.id).one()
    assert log.is_mock is True and log.session_id is None

    alerts = client.get("/api/v1/hr/gps-alerts", headers=auth_headers)
    assert alerts.status_code == 200, alerts.text
    mine = [a for a in alerts.json() if a["employee_id"] == emp.id]
    assert len(mine) == 1 and mine[0]["kind"] == "check-in" and mine[0]["device"] == "Pixel 9"


def test_real_location_still_checks_in(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    r = client.post("/api/v1/hr/check-in", headers=h, json={
        "latitude": NEAR_CLINIC[0], "longitude": NEAR_CLINIC[1], "accuracy": 5, "is_mock": False,
    })
    assert r.status_code == 200, r.text


def test_mock_ping_while_checked_in_counts_as_outside(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, NEAR_CLINIC).status_code == 200
    r = client.post("/api/v1/hr/location", headers=h, json={"latitude": CLINIC[0], "longitude": CLINIC[1], "is_mock": True})
    assert r.status_code == 200 and r.json()["inside_geofence"] is False
    kinds = [a["kind"] for a in client.get("/api/v1/hr/gps-alerts", headers=auth_headers).json() if a["employee_id"] == emp.id]
    assert kinds == ["while checked in"]


def test_gps_alerts_need_attendance_access(client, db, auth_headers):
    _cid, _emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert client.get("/api/v1/hr/gps-alerts", headers=h).status_code == 403


# ── signed-in devices ────────────────────────────────────────────────────────

def _remembered_employee(client, db, auth_headers, phones=2):
    from tests.test_ess_requests import _company_id, _ess_login

    cid = _company_id(client, auth_headers)
    username = f"dev.{_uid()}"
    emp, _ = _ess_login(client, db, auth_headers, cid, f"DEV-{_uid()}", username)
    tokens = []
    for i in range(phones):
        r = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123",
                                                    "remember_device": True, "device": f"Phone {i}"})
        tokens.append(r.json()["refresh_token"])
    return emp, tokens


def _refresh(client, token):
    return client.post("/api/v1/ess/refresh", json={"refresh_token": token}).status_code


def test_hr_lists_and_signs_out_one_phone(client, db, auth_headers):
    emp, (t0, t1) = _remembered_employee(client, db, auth_headers)
    r = client.get(f"/api/v1/hr/admin/employees/{emp.id}/devices", headers=auth_headers)
    assert r.status_code == 200, r.text
    devices = {d["device"]: d for d in r.json()}
    assert set(devices) == {"Phone 0", "Phone 1"} and all(d["active"] for d in devices.values())

    r = client.post(f"/api/v1/hr/admin/employees/{emp.id}/devices/{devices['Phone 0']['id']}/sign-out", headers=auth_headers)
    assert r.status_code == 200
    assert _refresh(client, t0) == 401
    assert _refresh(client, t1) == 200
    states = {d["device"]: d["status"] for d in client.get(f"/api/v1/hr/admin/employees/{emp.id}/devices", headers=auth_headers).json()}
    assert states == {"Phone 0": "Signed out", "Phone 1": "Signed in"}


def test_hr_signs_out_every_phone(client, db, auth_headers):
    emp, tokens = _remembered_employee(client, db, auth_headers, phones=3)
    r = client.post(f"/api/v1/hr/admin/employees/{emp.id}/devices/sign-out-all", headers=auth_headers)
    assert r.status_code == 200 and r.json()["signed_out"] == 3
    assert all(_refresh(client, t) == 401 for t in tokens)


def test_devices_of_another_company_are_not_reachable(client, db, auth_headers):
    emp, _ = _remembered_employee(client, db, auth_headers, phones=1)
    r = client.get(f"/api/v1/hr/admin/employees/{uuid.uuid4()}/devices", headers=auth_headers)
    assert r.status_code == 404
    row = db.query(EssRefreshToken).filter(EssRefreshToken.employee_id == emp.id).one()
    r = client.post(f"/api/v1/hr/admin/employees/{emp.id}/devices/{uuid.uuid4()}/sign-out", headers=auth_headers)
    assert r.status_code == 404
    assert row.revoked_at is None


def test_an_employee_cannot_see_devices(client, db, auth_headers):
    emp, _ = _remembered_employee(client, db, auth_headers, phones=1)
    _cid, _other, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert client.get(f"/api/v1/hr/admin/employees/{emp.id}/devices", headers=h).status_code == 403


# ── cleanup ──────────────────────────────────────────────────────────────────

def test_cleanup_deletes_only_tokens_that_can_never_work_again(client, db, auth_headers):
    emp, _ = _remembered_employee(client, db, auth_headers, phones=4)
    rows = db.query(EssRefreshToken).filter(EssRefreshToken.employee_id == emp.id).order_by(EssRefreshToken.device).all()
    now = datetime.now(UTC)
    rows[0].revoked_at = now - timedelta(days=31)          # signed out over a month ago -> deleted
    rows[1].revoked_at = now - timedelta(days=2)           # recently signed out -> kept (HR still sees it)
    rows[2].last_used_at = now - timedelta(days=125)       # dead for 90 + 30 days -> deleted
    # rows[3] in use -> kept
    db.commit()
    keep = {rows[1].id, rows[3].id}
    delete_dead_refresh_tokens(db, now)
    db.expire_all()
    left = {r.id for r in db.query(EssRefreshToken).filter(EssRefreshToken.employee_id == emp.id)}
    assert left == keep
