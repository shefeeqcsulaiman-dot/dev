"""GET /ess/leave-balance -- the Dashboard's Leave Balance widget. Same
entitlement/used/remaining math as HRMS's admin-only GET /leave/balance
(leave:view-gated), scoped to the calling employee instead."""
import json

from app.models import AppDataRecord, Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                    basic_salary=5000, status="active")
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


def test_ess_leave_balance_reflects_approved_days_and_is_scoped_to_self(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp_a, headers_a = _ess_login(client, db, auth_headers, company_id, "ESSBAL-A", "essbal.a")
    _emp_b, headers_b = _ess_login(client, db, auth_headers, company_id, "ESSBAL-B", "essbal.b")

    r = client.post("/api/v1/ess/leave", headers=headers_a, json={
        "leave_type": "Annual Leave", "start_date": "2026-10-05", "end_date": "2026-10-07", "reason": "Trip",
    })
    assert r.status_code == 201, r.text
    assert r.json()["days"] == 3
    request_id = r.json()["id"]

    r = client.post(f"/api/v1/leave/requests/{request_id}/approve", headers=auth_headers)
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/ess/leave-balance", headers=headers_a)
    assert r.status_code == 200, r.text
    annual = r.json()["by_type"]["Annual Leave"]
    assert annual["entitlement"] == 21   # default, unconfigured policy
    assert annual["used"] == 3
    assert annual["remaining"] == 18

    # B must NOT see A's usage through their own token -- still the full,
    # untouched entitlement.
    r = client.get("/api/v1/ess/leave-balance", headers=headers_b)
    assert r.status_code == 200, r.text
    b_annual = r.json()["by_type"]["Annual Leave"]
    assert b_annual["used"] == 0
    assert b_annual["remaining"] == 21


def test_ess_leave_balance_uses_configured_leave_type_cap(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSBAL-CAP", "essbal.cap")
    # Replace, don't just insert -- auth_headers reuses one shared test
    # company all session, and _leave_type_caps() reads this record via a
    # bare .first() with no ordering, so a leftover row from another test
    # file (e.g. test_leave.py's own raw insert under the same key) could
    # otherwise be the one that comes back instead of this one.
    db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "hrLeavePolicy",
        AppDataRecord.record_key == "leave-policy",
    ).delete()
    db.add(AppDataRecord(
        company_id=company_id, collection="hrLeavePolicy", record_key="leave-policy",
        payload=json.dumps({"leave_types": [{"type": "Sick Leave", "days": 10}]}),
    ))
    db.commit()

    try:
        r = client.get("/api/v1/ess/leave-balance", headers=headers)
        assert r.status_code == 200, r.text
        sick = r.json()["by_type"]["Sick Leave"]
        assert sick["entitlement"] == 10
        assert sick["used"] == 0
        assert sick["remaining"] == 10
    finally:
        # Other test files (e.g. test_leave.py) insert their own
        # hrLeavePolicy/leave-policy row for this same shared test company
        # without deleting first, trusting theirs to be the only/latest one
        # a later .first() lookup sees -- leaving this row behind would
        # silently break that assumption for whichever of those runs next.
        db.query(AppDataRecord).filter(
            AppDataRecord.company_id == company_id, AppDataRecord.collection == "hrLeavePolicy",
            AppDataRecord.record_key == "leave-policy",
        ).delete()
        db.commit()


def test_ess_leave_balance_annual_leave_falls_back_to_configured_type_cap(client, db, auth_headers):
    """An employee with no named leave policy assigned (#emp-leave-policy
    left blank on their Employee form -- the common case) previously fell
    back to a hardcoded 21 for Annual Leave, completely ignoring whatever
    HR had actually configured in HR Settings > Leave Types &
    Entitlements. It must use that configured cap instead, and only fall
    back to 21 if even that was never set."""
    company_id = _company_id(client, auth_headers)
    # _ess_login() creates a plain Employee row with no "employees"
    # AppDataRecord blob at all, so _employee_leave_policies() finds no
    # named policy for them -- exactly the "no policy assigned" case.
    emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSBAL-ANNUAL", "essbal.annual")

    db.query(AppDataRecord).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection == "hrLeavePolicy",
        AppDataRecord.record_key == "leave-policy",
    ).delete()
    db.add(AppDataRecord(
        company_id=company_id, collection="hrLeavePolicy", record_key="leave-policy",
        payload=json.dumps({"leave_types": [{"type": "Annual Leave", "days": 25}]}),
    ))
    db.commit()

    try:
        r = client.get("/api/v1/ess/leave-balance", headers=headers)
        assert r.status_code == 200, r.text
        annual = r.json()["by_type"]["Annual Leave"]
        assert annual["entitlement"] == 25
        assert annual["remaining"] == 25
    finally:
        db.query(AppDataRecord).filter(
            AppDataRecord.company_id == company_id, AppDataRecord.collection == "hrLeavePolicy",
            AppDataRecord.record_key == "leave-policy",
        ).delete()
        db.commit()
