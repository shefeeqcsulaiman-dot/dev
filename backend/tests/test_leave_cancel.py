"""Leave cancellation: HR cancels pending or approved leave in HRMS
(POST /leave/requests/{id}/cancel); an employee cancels pending leave or
approved leave that hasn't started yet from ESS (POST /ess/leave/{id}/cancel).
Cancelled leave keeps its record and gives the days back to the balance."""
import uuid
from datetime import date, timedelta

from app.models import Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_employee(client, db, admin_headers, permission_keys=()):
    suffix = uuid.uuid4().hex[:8]
    emp = Employee(company_id=_company_id(client, admin_headers), employee_no=f"LC-{suffix}",
                   full_name=f"Leave Cancel {suffix}", basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    body = {"username": f"lc.{suffix}", "password": "leavecancel123", "is_active": True}
    if permission_keys:
        r = client.post("/api/v1/hr/admin/roles", headers=admin_headers,
                        json={"role_name": f"LC role {suffix}", "description": "t", "permission_keys": list(permission_keys)})
        assert r.status_code == 201, r.text
        body["role_id"] = r.json()["id"]
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers, json=body)
    assert r.status_code == 200, r.text
    r = client.post("/api/v1/ess/login", json={"username": body["username"], "password": "leavecancel123"})
    assert r.status_code == 200, r.text
    return emp, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _request(client, headers, start, end, leave_type="Annual Leave"):
    r = client.post("/api/v1/ess/leave", headers=headers,
                    json={"leave_type": leave_type, "start_date": start.isoformat(), "end_date": end.isoformat(), "reason": "Trip"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _annual_used(client, headers):
    return client.get("/api/v1/ess/leave-balance", headers=headers).json()["by_type"]["Annual Leave"]["used"]


def _future(days):
    return date.today() + timedelta(days=days)


def test_hr_cancels_approved_leave_and_balance_is_restored(client, db, auth_headers):
    emp, ess = _ess_employee(client, db, auth_headers)
    rid = _request(client, ess, _future(30), _future(32))
    assert client.post(f"/api/v1/leave/requests/{rid}/approve", headers=auth_headers).status_code == 200
    used_before = _annual_used(client, ess)
    assert used_before >= 1

    r = client.post(f"/api/v1/leave/requests/{rid}/cancel", headers=auth_headers, json={"reason": "Plans changed"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "cancelled"
    assert out["cancel_reason"] == "Plans changed"
    assert out["cancelled_by"] and out["cancelled_at"]
    assert _annual_used(client, ess) == used_before - out["days"]

    # The record is kept (not deleted) and shows up as cancelled for the employee too.
    mine = {x["id"]: x for x in client.get("/api/v1/ess/leave", headers=ess).json()}
    assert mine[rid]["status"] == "cancelled" and mine[rid]["cancel_reason"] == "Plans changed"
    assert mine[rid]["can_cancel"] is False


def test_hr_can_cancel_pending_but_not_rejected_or_cancelled(client, db, auth_headers):
    _emp, ess = _ess_employee(client, db, auth_headers)
    pending = _request(client, ess, _future(40), _future(40))
    assert client.post(f"/api/v1/leave/requests/{pending}/cancel", headers=auth_headers).json()["status"] == "cancelled"
    assert client.post(f"/api/v1/leave/requests/{pending}/cancel", headers=auth_headers).status_code == 400

    rejected = _request(client, ess, _future(45), _future(45))
    client.post(f"/api/v1/leave/requests/{rejected}/reject", headers=auth_headers)
    r = client.post(f"/api/v1/leave/requests/{rejected}/cancel", headers=auth_headers)
    assert r.status_code == 400 and "rejected" in r.json()["detail"]


def test_hr_cancel_needs_leave_edit_permission(client, db, auth_headers):
    _emp, ess = _ess_employee(client, db, auth_headers)
    rid = _request(client, ess, _future(50), _future(50))
    _viewer, viewer_headers = _ess_employee(client, db, auth_headers, ["leave:view"])
    assert client.post(f"/api/v1/leave/requests/{rid}/cancel", headers=viewer_headers).status_code == 403


def test_employee_cancels_pending_and_upcoming_approved_leave(client, db, auth_headers):
    emp, ess = _ess_employee(client, db, auth_headers)
    pending = _request(client, ess, _future(60), _future(60))
    upcoming = _request(client, ess, _future(70), _future(71))
    client.post(f"/api/v1/leave/requests/{upcoming}/approve", headers=auth_headers)

    flags = {x["id"]: x["can_cancel"] for x in client.get("/api/v1/ess/leave", headers=ess).json()}
    assert flags[pending] is True and flags[upcoming] is True
    req_flags = {x["id"]: x["can_cancel"] for x in client.get("/api/v1/ess/requests", headers=ess).json() if x["kind"] == "leave"}
    assert req_flags[upcoming] is True

    assert client.post(f"/api/v1/ess/leave/{pending}/cancel", headers=ess).json()["status"] == "cancelled"
    r = client.post(f"/api/v1/ess/leave/{upcoming}/cancel", headers=ess)
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert r.json()["cancelled_by"] == emp.full_name


def test_employee_cannot_cancel_leave_that_has_started(client, db, auth_headers):
    _emp, ess = _ess_employee(client, db, auth_headers)
    started = _request(client, ess, date.today() - timedelta(days=1), _future(2))
    client.post(f"/api/v1/leave/requests/{started}/approve", headers=auth_headers)
    assert {x["id"]: x["can_cancel"] for x in client.get("/api/v1/ess/leave", headers=ess).json()}[started] is False
    r = client.post(f"/api/v1/ess/leave/{started}/cancel", headers=ess)
    assert r.status_code == 409 and "ask HR" in r.json()["detail"]
    # HR can still cancel it.
    assert client.post(f"/api/v1/leave/requests/{started}/cancel", headers=auth_headers).json()["status"] == "cancelled"


def test_employee_cannot_cancel_someone_elses_leave(client, db, auth_headers):
    _a, ess_a = _ess_employee(client, db, auth_headers)
    _b, ess_b = _ess_employee(client, db, auth_headers)
    rid = _request(client, ess_a, _future(80), _future(80))
    assert client.post(f"/api/v1/ess/leave/{rid}/cancel", headers=ess_b).status_code == 404


def test_approved_leave_cannot_be_deleted_points_to_cancel(client, db, auth_headers):
    _emp, ess = _ess_employee(client, db, auth_headers)
    rid = _request(client, ess, _future(90), _future(90))
    client.post(f"/api/v1/leave/requests/{rid}/approve", headers=auth_headers)
    r = client.delete(f"/api/v1/leave/requests/{rid}", headers=auth_headers)
    assert r.status_code == 400 and "cancel it instead" in r.json()["detail"]
