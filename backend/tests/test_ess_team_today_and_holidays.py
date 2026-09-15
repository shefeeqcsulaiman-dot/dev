"""GET /ess/team/today (today's Present/Absent/On Leave/Holiday status for
every ESS employee's own department, visible to every ESS user -- unlike
/ess/team which needs a department-scoped role) and GET /ess/holidays (the
shared company Holiday Calendar, read-only)."""
import json
from datetime import UTC, datetime

from app.attendance_store import _company_offset, upsert_attendance_event
from app.models import AppDataRecord, Employee, LeaveRequest


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username, department="Operations"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                    department=department, designation="Staff", basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": f"{username}pw123", "is_active": True},
    )
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return emp, headers


def test_team_today_shows_present_absent_and_leave_within_the_same_department(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp_present, headers = _ess_login(client, db, auth_headers, company_id, "TTODAY-PRESENT", "ttoday.present", department="TeamTodayDept")
    emp_absent, _ = _ess_login(client, db, auth_headers, company_id, "TTODAY-ABSENT", "ttoday.absent", department="TeamTodayDept")
    emp_leave, _ = _ess_login(client, db, auth_headers, company_id, "TTODAY-LEAVE", "ttoday.leave", department="TeamTodayDept")
    # A different department must never show up in this employee's team view.
    _ess_login(client, db, auth_headers, company_id, "TTODAY-OTHER", "ttoday.other", department="SomeOtherDept")

    upsert_attendance_event(db, company_id=company_id, employee_id=emp_present.employee_no,
                             punch_time=datetime.now(UTC), direction="in", source="manual")

    today = (datetime.now(UTC) + _company_offset(db, company_id)).date().isoformat()
    db.add(LeaveRequest(company_id=company_id, employee_id=emp_leave.id, leave_type="Annual Leave",
                         start_date=today, end_date=today, days=1, status="approved"))
    db.commit()

    r = client.get("/api/v1/ess/team/today", headers=headers)
    assert r.status_code == 200, r.text
    rows = {row["employee_no"]: row for row in r.json()}

    assert set(rows.keys()) == {"TTODAY-PRESENT", "TTODAY-ABSENT", "TTODAY-LEAVE"}
    assert rows["TTODAY-PRESENT"]["status"] == "present"
    assert rows["TTODAY-PRESENT"]["check_in"] is not None
    assert rows["TTODAY-ABSENT"]["status"] == "absent"
    assert rows["TTODAY-LEAVE"]["status"] == "leave"
    assert rows["TTODAY-PRESENT"]["is_me"] is True


def test_team_today_available_to_every_employee_no_special_role_needed(client, db, auth_headers):
    # This is the whole point of the endpoint: it must NOT require a
    # department-scoped role or hr:view_all_attendance permission the way
    # /ess/team and /hr/live-locations do.
    company_id = _company_id(client, auth_headers)
    _, headers = _ess_login(client, db, auth_headers, company_id, "TTODAY-SOLO", "ttoday.solo", department="SoloDept")
    r = client.get("/api/v1/ess/team/today", headers=headers)
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1


def test_holidays_visible_to_ess_and_sorted_by_date(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _, headers = _ess_login(client, db, auth_headers, company_id, "HOLCAL-1", "holcal.one")

    db.add_all([
        AppDataRecord(company_id=company_id, collection="hrHolidays", record_key="h2",
                      payload=json.dumps({"id": "h2", "date": "2026-12-25", "name": "Christmas", "location": "All branches", "paid": True})),
        AppDataRecord(company_id=company_id, collection="hrHolidays", record_key="h1",
                      payload=json.dumps({"id": "h1", "date": "2026-01-01", "name": "New Year", "location": "All branches", "paid": True})),
        AppDataRecord(company_id=company_id, collection="hrHolidays", record_key="hbad",
                      payload=json.dumps({"id": "hbad", "date": "not-a-date", "name": "Broken row"})),
    ])
    db.commit()

    r = client.get("/api/v1/ess/holidays", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    names = [row["name"] for row in rows]
    assert "Broken row" not in names
    assert names.index("New Year") < names.index("Christmas")


def test_holidays_requires_ess_auth(client):
    r = client.get("/api/v1/ess/holidays")
    assert r.status_code == 401
