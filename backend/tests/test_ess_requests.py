"""ESS self-service requests: cancel leave, update own task status, attendance
corrections, overtime / loan / salary-advance requests, the unified
/ess/requests list, contact-detail edits and document-expiry view.

Requests are written into the SAME AppDataRecord collections the HRMS
approval screens read, so these tests also check the record shape HRMS
expects and that the employee identity comes from the token, not the body."""
import json
from datetime import date, timedelta

from app.models import AppDataRecord, AuditLog, Employee, LeaveRequest


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                   basic_salary=5000, status="active", department="Ops")
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
    return emp, {"Authorization": f"Bearer {login.json()['access_token']}"}


def _records(db, company_id, collection):
    rows = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection).all()
    return [json.loads(r.payload) for r in rows]


# ── cancel a pending leave request ───────────────────────────────────────────

def test_cancel_pending_leave_marks_cancelled_and_keeps_history(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-L1", "essreq.l1")
    tomorrow = date.today() + timedelta(days=30)
    r = client.post("/api/v1/ess/leave", headers=h, json={"leave_type": "Annual Leave", "start_date": tomorrow.isoformat(), "end_date": tomorrow.isoformat()})
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]

    r = client.post(f"/api/v1/ess/leave/{leave_id}/cancel", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    # still listed (history), and the dates are free to be requested again
    assert [x["status"] for x in client.get("/api/v1/ess/leave", headers=h).json()] == ["cancelled"]
    again = client.post("/api/v1/ess/leave", headers=h, json={"leave_type": "Annual Leave", "start_date": tomorrow.isoformat(), "end_date": tomorrow.isoformat()})
    assert again.status_code == 201, again.text


def test_cannot_cancel_started_approved_or_someone_elses_leave(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp_a, ha = _ess_login(client, db, auth_headers, cid, "ESSREQ-L2", "essreq.l2")
    _emp_b, hb = _ess_login(client, db, auth_headers, cid, "ESSREQ-L3", "essreq.l3")
    d = (date.today() + timedelta(days=40)).isoformat()
    leave_id = client.post("/api/v1/ess/leave", headers=ha, json={"leave_type": "Sick Leave", "start_date": d, "end_date": d}).json()["id"]

    # another employee can't touch it (404, not 403 -- don't reveal it exists)
    assert client.post(f"/api/v1/ess/leave/{leave_id}/cancel", headers=hb).status_code == 404

    # Approved leave that has already started is HR's to cancel (upcoming approved
    # leave can be cancelled by the employee -- see test_leave_cancel.py).
    row = db.query(LeaveRequest).filter(LeaveRequest.id == leave_id).one()
    row.status = "approved"
    row.start_date = (date.today() - timedelta(days=1)).isoformat()
    db.commit()
    r = client.post(f"/api/v1/ess/leave/{leave_id}/cancel", headers=ha)
    assert r.status_code == 409
    assert "started" in r.json()["detail"]


# ── own task status ──────────────────────────────────────────────────────────

def _save_task(client, headers, task_id, assigned_to):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": "tasks", "record": {"id": task_id, "title": "Count stock", "assigned_to": assigned_to, "status": "todo", "priority": "High"}})
    assert r.status_code == 200, r.text


def test_employee_can_update_status_of_own_task_only(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp_a, ha = _ess_login(client, db, auth_headers, cid, "ESSREQ-T1", "essreq.t1")
    emp_b, _hb = _ess_login(client, db, auth_headers, cid, "ESSREQ-T2", "essreq.t2")
    _save_task(client, auth_headers, "TASK-ESS-A", emp_a.id)
    _save_task(client, auth_headers, "TASK-ESS-B", emp_b.id)

    r = client.patch("/api/v1/ess/tasks/TASK-ESS-A", headers=ha, json={"status": "progress"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "progress"
    assert r.json()["title"] == "Count stock"      # nothing else touched
    mine = client.get("/api/v1/ess/tasks", headers=ha).json()
    assert [t["status"] for t in mine if t["id"] == "TASK-ESS-A"] == ["progress"]

    # someone else's task, and an invalid status
    assert client.patch("/api/v1/ess/tasks/TASK-ESS-B", headers=ha, json={"status": "done"}).status_code == 404
    assert client.patch("/api/v1/ess/tasks/TASK-ESS-A", headers=ha, json={"status": "bogus"}).status_code == 422


def test_task_progress_follows_ess_status(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-T3", "essreq.t3")
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"},
                    json={"collection": "tasks", "record": {"id": "TASK-ESS-P", "title": "Audit shelf", "assigned_to": emp.id, "status": "progress", "progress": 40}})
    assert r.status_code == 200, r.text

    # Start/Back to To Do from an open task keeps the HR-set progress
    assert client.patch("/api/v1/ess/tasks/TASK-ESS-P", headers=h, json={"status": "todo"}).json()["progress"] == 40
    # Done is 100%, and reopening a Done task starts again at 0
    assert client.patch("/api/v1/ess/tasks/TASK-ESS-P", headers=h, json={"status": "done"}).json()["progress"] == 100
    assert client.patch("/api/v1/ess/tasks/TASK-ESS-P", headers=h, json={"status": "todo"}).json()["progress"] == 0
    stored = next(t for t in _records(db, cid, "tasks") if t["id"] == "TASK-ESS-P")
    assert (stored["status"], stored["progress"]) == ("todo", 0)


# ── attendance corrections ───────────────────────────────────────────────────

def test_correction_request_lands_in_hrms_collection_with_token_identity(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-C1", "essreq.c1")
    d = (date.today() - timedelta(days=2)).isoformat()
    r = client.post("/api/v1/ess/attendance-corrections", headers=h,
                    json={"date": d, "checkin": "09:00", "checkout": "17:30", "reason": "Forgot to punch out"})
    assert r.status_code == 201, r.text

    rec = next(x for x in _records(db, cid, "attendanceCorrections") if x["id"] == r.json()["id"])
    assert rec["employee_id"] == "ESSREQ-C1" and rec["employee"] == emp.full_name
    assert rec["status"] == "Pending" and rec["date"] == d
    assert rec["checkin"] == "09:00" and rec["checkout"] == "17:30" and rec["source"] == "ess"


def test_correction_request_validation(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-C2", "essreq.c2")
    today = date.today()
    ok = {"date": (today - timedelta(days=1)).isoformat(), "checkin": "09:00", "reason": "x"}

    def post(**over):
        return client.post("/api/v1/ess/attendance-corrections", headers=h, json={**ok, **over})

    assert post(date=(today + timedelta(days=3)).isoformat()).status_code == 400        # future
    assert post(date=(today - timedelta(days=90)).isoformat()).status_code == 400       # too old
    assert post(checkin=None, checkout=None).status_code == 400                         # no times
    assert post(checkin="9am").status_code == 400                                       # bad format
    assert post(checkin="17:00", checkout="09:00").status_code == 400                   # out before in
    assert post(reason="   ").status_code == 400                                        # blank reason
    assert post().status_code == 201
    assert post().status_code == 409                                                    # duplicate pending for that day


# ── overtime ─────────────────────────────────────────────────────────────────

def test_overtime_hours_from_times_and_multiplier_from_company_settings(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-O1", "essreq.o1")
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"},
                    json={"collection": "hr_settings", "record": {"id": "ot-rules-config", "multNormal": "1.4", "multWeekend": "2", "multHoliday": "2.5", "multRamadan": "1.1"}})
    assert r.status_code == 200, r.text
    try:
        d = (date.today() - timedelta(days=1)).isoformat()

        r = client.post("/api/v1/ess/overtime", headers=h, json={"date": d, "login": "18:00", "logout": "20:30", "ot_type": "holiday", "reason": "Stock take"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["ot_hours"] == "2.5"
        assert body["multiplier"] == "2.5×"        # the company's holiday rate -- not chosen by the employee
        assert body["status"] == "Pending" and body["employee_id"] == "ESSREQ-O1"
        assert any(x["id"] == body["id"] for x in _records(db, cid, "overtimeRequests"))

        # a shift crossing midnight
        r = client.post("/api/v1/ess/overtime", headers=h, json={"date": d, "login": "22:00", "logout": "01:00", "ot_type": "normal"})
        assert r.status_code == 201 and r.json()["ot_hours"] == "3.0" and r.json()["multiplier"] == "1.4×"
    finally:
        # The test company is shared across the whole session, and test_payroll seeds its OWN
        # "ot-rules-config" row directly -- a leftover one from here would win its .first() lookup.
        db.query(AppDataRecord).filter(
            AppDataRecord.company_id == cid, AppDataRecord.collection == "hr_settings", AppDataRecord.record_key == "ot-rules-config",
        ).delete()
        db.commit()


def test_overtime_validation(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-O2", "essreq.o2")
    # "yesterday" (not today) as the valid date: the company's local day can be
    # ahead of this machine's, and a same-day date must never read as "future".
    ok_day = (date.today() - timedelta(days=1)).isoformat()

    def post(**body):
        return client.post("/api/v1/ess/overtime", headers=h, json=body)

    assert post(date=(date.today() + timedelta(days=3)).isoformat(), ot_hours=2).status_code == 400
    assert post(date=(date.today() - timedelta(days=60)).isoformat(), ot_hours=2).status_code == 400
    assert post(date=ok_day).status_code == 400                       # no hours, no times
    assert post(date=ok_day, ot_hours=0).status_code == 400
    assert post(date=ok_day, ot_hours=13).status_code == 400
    assert post(date=ok_day, ot_hours=2, ot_type="bogus").status_code == 422
    assert post(date=ok_day, ot_hours=2).status_code == 201


def test_pending_request_cap_per_kind(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-O3", "essreq.o3")
    today = (date.today() - timedelta(days=1)).isoformat()
    for _ in range(10):
        assert client.post("/api/v1/ess/advances", headers=h, json={"amount": 100}).status_code == 201
    r = client.post("/api/v1/ess/advances", headers=h, json={"amount": 100})
    assert r.status_code == 409 and "pending" in r.json()["detail"]
    # another kind is unaffected
    assert client.post("/api/v1/ess/overtime", headers=h, json={"date": today, "ot_hours": 1}).status_code == 201


# ── loans & advances ─────────────────────────────────────────────────────────

def test_loan_request_computes_emi_and_balance(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-N1", "essreq.n1")
    r = client.post("/api/v1/ess/loans", headers=h, json={"type": "Medical Loan", "amount": 6000, "months": 6, "reason": "Surgery"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["emi"] == 1000.0 and body["balance"] == 6000.0 and body["status"] == "Pending"
    assert any(x["id"] == body["id"] for x in _records(db, cid, "employeeLoans"))

    assert client.post("/api/v1/ess/loans", headers=h, json={"type": "Yacht Loan", "amount": 100, "months": 3}).status_code == 400
    assert client.post("/api/v1/ess/loans", headers=h, json={"amount": 0, "months": 3}).status_code == 400
    assert client.post("/api/v1/ess/loans", headers=h, json={"amount": 100, "months": 0}).status_code == 400
    assert client.post("/api/v1/ess/loans", headers=h, json={"amount": 100, "months": 61}).status_code == 400


def test_advance_request_defaults_and_validates_month(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-N2", "essreq.n2")
    r = client.post("/api/v1/ess/advances", headers=h, json={"amount": 750.5, "reason": "Rent"})
    assert r.status_code == 201, r.text
    assert r.json()["month"] == date.today().strftime("%Y-%m")
    assert client.post("/api/v1/ess/advances", headers=h, json={"amount": 100, "month": "2026-13"}).status_code == 400
    assert client.post("/api/v1/ess/advances", headers=h, json={"amount": -5}).status_code == 400


# ── unified requests list ────────────────────────────────────────────────────

def test_requests_list_merges_kinds_and_is_scoped_to_the_employee(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _a, ha = _ess_login(client, db, auth_headers, cid, "ESSREQ-R1", "essreq.r1")
    _b, hb = _ess_login(client, db, auth_headers, cid, "ESSREQ-R2", "essreq.r2")
    d = (date.today() + timedelta(days=50)).isoformat()
    client.post("/api/v1/ess/leave", headers=ha, json={"leave_type": "Annual Leave", "start_date": d, "end_date": d})
    client.post("/api/v1/ess/loans", headers=ha, json={"amount": 1200, "months": 4})
    client.post("/api/v1/ess/advances", headers=ha, json={"amount": 300})
    client.post("/api/v1/ess/overtime", headers=ha, json={"date": (date.today() - timedelta(days=1)).isoformat(), "ot_hours": 2})
    client.post("/api/v1/ess/attendance-corrections", headers=ha, json={"date": (date.today() - timedelta(days=1)).isoformat(), "checkout": "18:00", "reason": "x"})
    client.post("/api/v1/ess/advances", headers=hb, json={"amount": 999})

    mine = client.get("/api/v1/ess/requests", headers=ha).json()
    assert sorted(x["kind"] for x in mine) == ["advance", "correction", "leave", "loan", "overtime"]
    assert all(x["status"] == "pending" for x in mine)
    assert next(x for x in mine if x["kind"] == "leave")["can_cancel"] is True
    assert [x["amount"] for x in client.get("/api/v1/ess/requests", headers=hb).json()] == [999.0]


def test_requests_require_a_valid_ess_token(client):
    # Valid bodies, so the 401 is what's being tested (body validation would
    # otherwise answer 422 first).
    cases = (
        ("get", "/api/v1/ess/requests", None), ("get", "/api/v1/ess/documents", None), ("get", "/api/v1/ess/profile-details", None),
        ("post", "/api/v1/ess/loans", {"amount": 100, "months": 2}), ("post", "/api/v1/ess/advances", {"amount": 100}),
        ("post", "/api/v1/ess/overtime", {"date": date.today().isoformat(), "ot_hours": 1}),
        ("post", "/api/v1/ess/attendance-corrections", {"date": date.today().isoformat(), "checkin": "09:00", "reason": "x"}),
        ("put", "/api/v1/ess/profile-details", {"mobile": "0501234567"}), ("patch", "/api/v1/ess/tasks/x", {"status": "done"}),
        ("post", "/api/v1/ess/leave/x/cancel", None),
    )
    for method, path, body in cases:
        kwargs = {"json": body} if body is not None else {}
        assert getattr(client, method)(path, **kwargs).status_code == 401, path


# ── contact details & documents ──────────────────────────────────────────────

def _save_employee_record(client, headers, employee_no, name, **extra):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": "employees", "record": {"id": employee_no, "name": name, "department": "Ops", "designation": "Staff", "status": "Active", **extra}})
    assert r.status_code == 200, r.text


def test_employee_can_edit_only_contact_fields_and_change_is_audited(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-P1", "essreq.p1")
    _save_employee_record(client, auth_headers, "ESSREQ-P1", emp.full_name, mobile="0500000000", salary=9999, iban="AE070331234567890123456")

    assert client.get("/api/v1/ess/profile-details", headers=h).json()["mobile"] == "0500000000"
    r = client.put("/api/v1/ess/profile-details", headers=h, json={"mobile": "+971 50 111 2222", "emergency_contact": "Mum", "emergency_mobile": "0501234567", "address": "Dubai"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == ["address", "emergency_contact", "emergency_mobile", "mobile"]

    got = client.get("/api/v1/ess/profile-details", headers=h).json()
    assert got["mobile"] == "+971 50 111 2222" and got["emergency_contact"] == "Mum" and got["address"] == "Dubai"
    stored = next(x for x in _records(db, cid, "employees") if x["id"] == "ESSREQ-P1")
    assert stored["iban"] == "AE070331234567890123456" and stored["salary"] == 9999          # untouched
    log = db.query(AuditLog).filter(AuditLog.employee_id == emp.id, AuditLog.action == "profile_contact_updated").one()
    assert "0500000000" in log.detail and "+971 50 111 2222" in log.detail

    # a body that tries to smuggle other fields is ignored; junk phone numbers are rejected
    client.put("/api/v1/ess/profile-details", headers=h, json={"salary": 1, "iban": "X", "name": "Hacker"})
    stored = next(x for x in _records(db, cid, "employees") if x["id"] == "ESSREQ-P1")
    assert stored["iban"] == "AE070331234567890123456" and stored["salary"] == 9999 and stored["name"] == emp.full_name
    assert client.put("/api/v1/ess/profile-details", headers=h, json={"mobile": "call me maybe"}).status_code == 400
    assert client.put("/api/v1/ess/profile-details", headers=h, json={"address": "x" * 301}).status_code == 400


def test_profile_edit_without_an_hr_record_is_a_clear_409(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-P2", "essreq.p2")
    assert client.get("/api/v1/ess/profile-details", headers=h).json()["has_record"] is False
    assert client.put("/api/v1/ess/profile-details", headers=h, json={"mobile": "0501234567"}).status_code == 409


def test_documents_states_match_hrms_expiry_thresholds(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-D1", "essreq.d1")
    today = date.today()
    _save_employee_record(
        client, auth_headers, "ESSREQ-D1", emp.full_name,
        # mid-band values: the company's local day can differ from this machine's by a day
        visa_expiry=(today - timedelta(days=5)).isoformat(),        # expired
        passport_expiry=(today + timedelta(days=15)).isoformat(),   # critical (<=30)
        eid_expiry=(today + timedelta(days=60)).isoformat(),        # soon (<=90)
        insurance_expiry=(today + timedelta(days=200)).isoformat(),  # valid
    )
    docs = {d["label"]: d for d in client.get("/api/v1/ess/documents", headers=h).json()}
    assert docs["Visa / Work Permit"]["state"] == "expired" and docs["Visa / Work Permit"]["days_left"] < 0
    assert docs["Passport"]["state"] == "critical"
    assert docs["Emirates ID"]["state"] == "soon"
    assert docs["Insurance"]["state"] == "valid"
    assert docs["Labor Card"]["state"] == "missing" and docs["Labor Card"]["expiry"] is None


def test_ess_overtime_eligibility_is_own_and_flips_after_request(client, db, auth_headers):
    from datetime import datetime, timezone
    from app import attendance_store
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "ESSREQ-OT9", "essreq.ot9")
    other = Employee(company_id=cid, employee_no="ESSREQ-OT9B", full_name="Other", basic_salary=1, status="active")
    db.add(other)
    db.commit()
    day = date.today() - timedelta(days=2)
    for no in ("ESSREQ-OT9", "ESSREQ-OT9B"):
        for hour, direction in ((6, "in"), (16, "out")):
            attendance_store.upsert_attendance_event(
                db, company_id=cid, employee_id=no, direction=direction, source="device",
                punch_time=datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc))
    rows = client.get("/api/v1/ess/overtime-eligibility", headers=h).json()["rows"]
    assert [r["employee_no"] for r in rows] == ["ESSREQ-OT9"]
    assert rows[0]["eligible"] and rows[0]["clock_in"] and rows[0]["clock_out"]
    r = client.post("/api/v1/ess/overtime", headers=h, json={"date": day.isoformat(), "ot_hours": 2})
    assert r.status_code == 201, r.text
    rows = client.get("/api/v1/ess/overtime-eligibility", headers=h).json()["rows"]
    assert rows[0]["eligibility"] == "Requested - Pending"
