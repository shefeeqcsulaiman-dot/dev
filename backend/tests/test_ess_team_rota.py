"""GET /ess/team/rota -- an employee's own department's rota for one
Mon-Sun week, backing the ESS Rota tab's "Department Rota" view. Same
"own department, everyone, no role-scope required" reach as
/ess/team/today and /ess/team/leave."""
import json

from app.models import AppDataRecord, Employee


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
        json={"username": username, "password": "teamrota123", "is_active": True},
    )
    assert r.status_code == 200, r.text


def _login(client, username):
    r = client.post("/api/v1/ess/login", json={"username": username, "password": "teamrota123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_rota(db, company_id, emp_no, work_date, code="M", start="09:00", end="17:00", tasks=None):
    record_id = f"{emp_no}-{work_date}"
    payload = {
        "id": record_id, "employee_id": emp_no, "date": work_date, "day": "Mon",
        "code": code, "start": start, "end": end, "status": "Published",
        "tasks": tasks or [],
    }
    db.add(AppDataRecord(company_id=company_id, collection="rotaAssignments", record_key=record_id, payload=json.dumps(payload)))
    db.commit()


def test_ess_team_rota_returns_own_department_peers_and_assignments(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TR-VIEWER-1", "Viewer One", department="Logistics")
    peer = _seed_employee(db, company_id, "TR-PEER-1", "Peer One", department="Logistics")
    other_dept = _seed_employee(db, company_id, "TR-OTHER-1", "Other Dept", department="Finance")

    _seed_rota(db, company_id, "TR-PEER-1", "2027-03-08", tasks=[{"task_id": "t1", "title": "Stock Count", "color": "#2563eb", "start": "10:00", "end": "11:00"}])
    _seed_rota(db, company_id, "TR-OTHER-1", "2027-03-08")

    _grant_portal_access(client, auth_headers, viewer.id, "tr.viewer1")
    headers = _login(client, "tr.viewer1")

    r = client.get("/api/v1/ess/team/rota", headers=headers, params={"week": "2027-03-08"})
    assert r.status_code == 200, r.text
    body = r.json()
    names = {e["full_name"] for e in body["employees"]}
    assert "Peer One" in names
    assert "Other Dept" not in names

    assignment_emps = {a["employee_id"] for a in body["assignments"]}
    assert "TR-PEER-1" in assignment_emps
    assert "TR-OTHER-1" not in assignment_emps

    peer_assignment = next(a for a in body["assignments"] if a["employee_id"] == "TR-PEER-1")
    assert peer_assignment["tasks"][0]["title"] == "Stock Count"


def test_ess_team_rota_excludes_assignments_outside_the_week(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TR-VIEWER-2", "Viewer Two", department="Sales")
    peer = _seed_employee(db, company_id, "TR-PEER-2", "Peer Two", department="Sales")
    _seed_rota(db, company_id, "TR-PEER-2", "2027-04-01")

    _grant_portal_access(client, auth_headers, viewer.id, "tr.viewer2")
    headers = _login(client, "tr.viewer2")

    r = client.get("/api/v1/ess/team/rota", headers=headers, params={"week": "2027-04-05"})
    assert r.status_code == 200, r.text
    assert r.json()["assignments"] == []


def test_ess_team_rota_excludes_inactive_employees(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TR-VIEWER-3", "Viewer Three", department="Marketing")
    _seed_employee(db, company_id, "TR-INACTIVE-1", "Former Peer", department="Marketing", status="inactive")

    _grant_portal_access(client, auth_headers, viewer.id, "tr.viewer3")
    headers = _login(client, "tr.viewer3")

    r = client.get("/api/v1/ess/team/rota", headers=headers, params={"week": "2027-05-03"})
    assert r.status_code == 200, r.text
    names = {e["full_name"] for e in r.json()["employees"]}
    assert "Former Peer" not in names
    assert "Viewer Three" in names


def test_ess_team_rota_invalid_week_returns_400(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    viewer = _seed_employee(db, company_id, "TR-VIEWER-4", "Viewer Four", department="Ops")
    _grant_portal_access(client, auth_headers, viewer.id, "tr.viewer4")
    headers = _login(client, "tr.viewer4")

    r = client.get("/api/v1/ess/team/rota", headers=headers, params={"week": "not-a-date"})
    assert r.status_code == 400, r.text
