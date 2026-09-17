"""GET /ess/team/leave -- approved leave for an employee's own department
peers within a given month, backing the Team Holiday Calendar's
calendar-view overlay in ess.html. Same "own department, everyone, no
role-scope required" reach as /ess/team/today (not the narrower
role-scoped /ess/team)."""
from app.models import Employee, LeaveRequest


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _seed_employee(db, company_id, emp_no, name, department="Operations", status="active"):
    emp = Employee(company_id=company_id, employee_no=emp_no, full_name=name, department=department, status=status)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _grant_portal_access(client, headers, employee_id, username):
    r = client.put(
        f"/api/v1/hr/admin/employees/{employee_id}/portal-access",
        headers=headers,
        json={"username": username, "password": "teamleave123", "is_active": True},
    )
    assert r.status_code == 200, r.text


def _login(client, username):
    r = client.post("/api/v1/ess/login", json={"username": username, "password": "teamleave123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_leave(db, company_id, employee_id, start_date, end_date, status="approved", leave_type="Annual Leave"):
    lr = LeaveRequest(
        company_id=company_id, employee_id=employee_id, leave_type=leave_type,
        start_date=start_date, end_date=end_date, days=1, status=status,
    )
    db.add(lr)
    db.commit()
    return lr


def test_ess_team_leave_returns_approved_leave_for_own_department(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TL-VIEWER-1", "Viewer One", department="Logistics")
    peer = _seed_employee(db, company_id, "TL-PEER-1", "Peer One", department="Logistics")
    other_dept = _seed_employee(db, company_id, "TL-OTHER-1", "Other Dept", department="Finance")

    _seed_leave(db, company_id, peer.id, "2027-03-10", "2027-03-12")
    _seed_leave(db, company_id, other_dept.id, "2027-03-10", "2027-03-12")

    _grant_portal_access(client, auth_headers, viewer.id, "tl.viewer1")
    headers = _login(client, "tl.viewer1")

    r = client.get("/api/v1/ess/team/leave", headers=headers, params={"month": "2027-03"})
    assert r.status_code == 200, r.text
    rows = r.json()
    names = {row["employee_name"] for row in rows}
    assert "Peer One" in names
    assert "Other Dept" not in names


def test_ess_team_leave_excludes_pending_and_rejected(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TL-VIEWER-2", "Viewer Two", department="Sales")
    pending_peer = _seed_employee(db, company_id, "TL-PEER-2", "Pending Peer", department="Sales")
    rejected_peer = _seed_employee(db, company_id, "TL-PEER-3", "Rejected Peer", department="Sales")

    _seed_leave(db, company_id, pending_peer.id, "2027-04-05", "2027-04-06", status="pending")
    _seed_leave(db, company_id, rejected_peer.id, "2027-04-05", "2027-04-06", status="rejected")

    _grant_portal_access(client, auth_headers, viewer.id, "tl.viewer2")
    headers = _login(client, "tl.viewer2")

    r = client.get("/api/v1/ess/team/leave", headers=headers, params={"month": "2027-04"})
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_ess_team_leave_excludes_leave_outside_the_requested_month(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TL-VIEWER-3", "Viewer Three", department="Marketing")
    peer = _seed_employee(db, company_id, "TL-PEER-4", "Marketing Peer", department="Marketing")
    _seed_leave(db, company_id, peer.id, "2027-05-01", "2027-05-03")

    _grant_portal_access(client, auth_headers, viewer.id, "tl.viewer3")
    headers = _login(client, "tl.viewer3")

    r = client.get("/api/v1/ess/team/leave", headers=headers, params={"month": "2027-06"})
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_ess_team_leave_invalid_month_returns_400(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TL-VIEWER-4", "Viewer Four", department="Ops")
    _grant_portal_access(client, auth_headers, viewer.id, "tl.viewer4")
    headers = _login(client, "tl.viewer4")

    r = client.get("/api/v1/ess/team/leave", headers=headers, params={"month": "not-a-month"})
    assert r.status_code == 400, r.text
