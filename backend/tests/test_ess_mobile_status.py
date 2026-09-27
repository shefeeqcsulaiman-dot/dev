"""/hr/dashboard always carries the caller's own GPS check-in status (the ESS
phone check-in button reads it), including for Manager / HR roles whose
dashboards are otherwise team summaries."""
from datetime import UTC, datetime, timedelta

from app.models import AttendanceSession, Role
from tests.test_ess_requests import _company_id, _ess_login


def test_manager_dashboard_includes_own_check_in_status(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "MOB-MGR1", "mob.mgr1")
    role = Role(company_id=cid, role_name="Manager", is_system_role=False, department_scope=None)
    db.add(role)
    db.flush()
    emp.role_id = role.id
    db.commit()

    body = client.get("/api/v1/hr/dashboard", headers=h).json()
    assert body["role"] == "Manager"
    assert body["checked_in"] is False and body["today_sessions"] == []
    assert "team_active_sessions" in body  # the manager summary is still there

    start = datetime.now(UTC) - timedelta(minutes=5)
    db.add(AttendanceSession(company_id=cid, employee_id=emp.id, check_in=start, status="open"))
    db.commit()
    body = client.get("/api/v1/hr/dashboard", headers=h).json()
    assert body["checked_in"] is True
    assert body["check_in_at"].endswith("+00:00")
    assert len(body["today_sessions"]) == 1 and body["today_sessions"][0]["check_out"] is None


def test_employee_dashboard_lists_only_own_sessions(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "MOB-EMP1", "mob.emp1")
    other, _ = _ess_login(client, db, auth_headers, cid, "MOB-EMP2", "mob.emp2")
    now = datetime.now(UTC)
    db.add(AttendanceSession(company_id=cid, employee_id=other.id, check_in=now - timedelta(hours=1), status="open"))
    db.add(AttendanceSession(company_id=cid, employee_id=emp.id, check_in=now - timedelta(hours=3),
                             check_out=now - timedelta(hours=2), status="closed"))
    db.commit()
    body = client.get("/api/v1/hr/dashboard", headers=h).json()
    assert body["checked_in"] is False
    assert len(body["today_sessions"]) <= 1  # may be 0 if the local day started less than 3h ago
    assert all(s["check_out"] for s in body["today_sessions"])
