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


def _weekend_rows(db, company_id):
    return db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "hr_settings",
                                          AppDataRecord.record_key == "weekend-policy-config").all()


def _every_day_is_a_working_day(db, company_id):
    """'custom' weekend policy = no weekend, so this test passes on any weekday (other tests leave
    the shared company on a Fri/Sat weekend, sometimes in more than one row: every row is set).
    Returns the previous payloads to restore."""
    rows = _weekend_rows(db, company_id)
    previous = [(row.id, row.payload) for row in rows]
    if not rows:
        row = AppDataRecord(company_id=company_id, collection="hr_settings", record_key="weekend-policy-config")
        db.add(row)
        rows = [row]
    for row in rows:
        row.payload = json.dumps({"mode": "custom"})
    db.commit()
    return previous


def _restore_weekend_policy(db, company_id, previous):
    saved = dict(previous)
    for row in _weekend_rows(db, company_id):
        if row.id in saved:
            row.payload = saved[row.id]
        else:
            db.delete(row)
    db.commit()


def test_team_today_shows_present_absent_and_leave_within_the_same_department(client, db, auth_headers, monkeypatch):
    # Work starts at midnight with no grace: anyone without a punch is already "absent".
    monkeypatch.setattr("app.routers.ess._late_rules", lambda db, company_id: ("00:00", 0))
    company_id = _company_id(client, auth_headers)
    previous_policy = _every_day_is_a_working_day(db, company_id)
    try:
        _check_team_today(client, db, auth_headers, company_id)
    finally:
        _restore_weekend_policy(db, company_id, previous_policy)


def _check_team_today(client, db, auth_headers, company_id):
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


def test_no_punch_before_the_start_time_is_not_started_not_absent(client, db, auth_headers, monkeypatch):
    # Work starts at 23:59: an employee with no punch yet hasn't started, they aren't absent.
    monkeypatch.setattr("app.routers.ess._late_rules", lambda db, company_id: ("23:59", 0))
    company_id = _company_id(client, auth_headers)
    previous_policy = _every_day_is_a_working_day(db, company_id)
    try:
        _, headers = _ess_login(client, db, auth_headers, company_id, "TTODAY-LATER", "ttoday.later", department="LaterDept")
        r = client.get("/api/v1/ess/team/today", headers=headers)
        assert r.status_code == 200, r.text
        row = r.json()[0]
        assert row["status"] == "upcoming"
        assert row["starts_at"] == "23:59"
    finally:
        _restore_weekend_policy(db, company_id, previous_policy)


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


def test_team_today_shows_rota_days_off(client, db, auth_headers, monkeypatch):
    monkeypatch.setattr("app.routers.ess._late_rules", lambda db, company_id: ("00:00", 0))
    from datetime import timedelta
    company_id = _company_id(client, auth_headers)
    me, headers = _ess_login(client, db, auth_headers, company_id, "TOFF-EXPLICIT", "toff.explicit", department="TeamOffDept")
    gap, _ = _ess_login(client, db, auth_headers, company_id, "TOFF-GAP", "toff.gap", department="TeamOffDept")
    unscheduled, _ = _ess_login(client, db, auth_headers, company_id, "TOFF-NONE", "toff.none", department="TeamOffDept")
    worked, _ = _ess_login(client, db, auth_headers, company_id, "TOFF-WORKED", "toff.worked", department="TeamOffDept")

    today = (datetime.now(UTC) + _company_offset(db, company_id)).date()
    other_day = today + timedelta(days=1) if today.weekday() < 6 else today - timedelta(days=1)  # same Mon-Sun week

    def rota(emp, day, **fields):
        rec = {"id": f"{emp.employee_no}-{day}", "employee_id": emp.employee_no, "date": day.isoformat(), **fields}
        r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "rotaAssignments", "record": rec})
        assert r.status_code == 200, r.text

    rota(me, today, code="OFF", mark="Off", type="Off")
    rota(gap, other_day, code="M", mark="Shift", start="09:00", end="17:00")
    rota(worked, today, code="OFF", mark="Off", type="Off")
    upsert_attendance_event(db, company_id=company_id, employee_id=worked.employee_no,
                             punch_time=datetime.now(UTC), direction="in", source="manual")

    rows = {row["employee_no"]: row["status"] for row in client.get("/api/v1/ess/team/today", headers=headers).json()}
    if rows["TOFF-NONE"] in ("weekend", "holiday"):
        return  # company-wide day off today; rota statuses don't apply
    assert rows["TOFF-EXPLICIT"] == "off"
    assert rows["TOFF-GAP"] == "off"
    assert rows["TOFF-NONE"] == "absent"
    assert rows["TOFF-WORKED"] == "present"  # checked in on a day off still counts as present


def test_team_today_uses_rota_leave_mark(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp, headers = _ess_login(client, db, auth_headers, company_id, "TOFF-LMARK", "toff.lmark", department="TeamLeaveMarkDept")
    today = (datetime.now(UTC) + _company_offset(db, company_id)).date().isoformat()
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "rotaAssignments",
        "record": {"id": f"TOFF-LMARK-{today}", "employee_id": "TOFF-LMARK", "date": today, "code": "L", "mark": "Leave"}})
    assert r.status_code == 200, r.text
    status = client.get("/api/v1/ess/team/today", headers=headers).json()[0]["status"]
    assert status in ("leave", "weekend", "holiday")


def test_team_today_links_each_colleague_photo(client, db, auth_headers):
    """The team list carries a link per photo (not the base64 itself); the link serves the image,
    cached for good, and a wrong or stale version is a 404."""
    import base64

    company_id = _company_id(client, auth_headers)
    me, headers = _ess_login(client, db, auth_headers, company_id, "TPHOTO-ME", "tphoto.me", department="PhotoDept")
    peer, _ = _ess_login(client, db, auth_headers, company_id, "TPHOTO-PEER", "tphoto.peer", department="PhotoDept")
    png = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    peer.photo = "data:image/png;base64," + base64.b64encode(png).decode()
    db.commit()

    rows = {r["employee_no"]: r for r in client.get("/api/v1/ess/team/today", headers=headers).json()}
    assert rows["TPHOTO-ME"]["photo_url"] is None
    url = rows["TPHOTO-PEER"]["photo_url"]
    assert url.startswith(f"/api/v1/ess/team-photo/{peer.id}?v=") and "base64" not in str(rows)

    img = client.get(url)
    assert img.status_code == 200 and img.content == png and img.headers["content-type"] == "image/png"
    assert "immutable" in img.headers["cache-control"]
    assert client.get(url, headers={"If-None-Match": img.headers["etag"]}).status_code == 304
    assert client.get(url.split("?")[0] + "?v=0000000000000000").status_code == 404
    assert client.get(f"/api/v1/ess/team-photo/{me.id}?v=x").status_code == 404
