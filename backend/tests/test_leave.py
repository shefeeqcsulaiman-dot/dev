"""Regression coverage for the HRMS leave-approval bugs found in the deep
HRMS audit:
  - approving a leave request that starts in a future year previously
    drained the CURRENT year's balance forever (_used_days_for_type had no
    upper bound on start_date);
  - only "Annual Leave" ever had its cap enforced at approval time — every
    other configured leave type (Sick, Emergency, Maternity, Paternity,
    Hajj) could be approved without limit;
  - an approved leave request could be deleted outright, silently restoring
    the entitlement it had already consumed with no trace.
"""
from datetime import datetime, timezone

from app.models import Employee, LeaveRequest


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no="LV-TEST-001", full_name="Leave Test Employee"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _seed_leave_request(db, company_id, employee_id, leave_type, start_date, end_date, days, status="pending"):
    req = LeaveRequest(company_id=company_id, employee_id=employee_id, leave_type=leave_type,
                        start_date=start_date, end_date=end_date, days=days, status=status)
    db.add(req)
    db.commit()
    db.refresh(req)
    return req


def test_leave_balance_excludes_approved_requests_starting_a_future_year(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, employee_no="LV-TEST-FUTURE")
    year = datetime.now(timezone.utc).year
    # Previously counted against THIS year's balance forever, since the
    # "used" query only checked `start_date >= this_year-01-01` with no
    # upper bound at all.
    _seed_leave_request(db, company_id, emp.id, "Sick Leave",
                         f"{year + 1}-01-10", f"{year + 1}-01-15", 6, status="approved")

    r = client.get("/api/v1/leave/balance", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(b for b in r.json() if b["employee_id"] == emp.id)
    assert row["by_type"]["Sick Leave"]["used"] == 0


def test_leave_approval_enforces_non_annual_type_cap(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, employee_no="LV-TEST-CAP")
    year = datetime.now(timezone.utc).year
    # Default Sick Leave cap is 90 days (see _DEFAULT_LEAVE_TYPE_CAPS) —
    # previously nothing checked this at all for any type but Annual Leave.
    over_cap = _seed_leave_request(db, company_id, emp.id, "Sick Leave",
                                    f"{year}-02-01", f"{year}-05-10", 99, status="pending")
    r = client.post(f"/api/v1/leave/requests/{over_cap.id}/approve", headers=auth_headers)
    assert r.status_code == 409, r.text

    within_cap = _seed_leave_request(db, company_id, emp.id, "Sick Leave",
                                      f"{year}-06-01", f"{year}-06-10", 10, status="pending")
    r2 = client.post(f"/api/v1/leave/requests/{within_cap.id}/approve", headers=auth_headers)
    assert r2.status_code == 200, r2.text


def test_delete_leave_request_blocks_approved_but_allows_pending(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, employee_no="LV-TEST-DEL")
    year = datetime.now(timezone.utc).year
    approved = _seed_leave_request(db, company_id, emp.id, "Annual Leave",
                                    f"{year}-03-01", f"{year}-03-03", 3, status="approved")
    r = client.delete(f"/api/v1/leave/requests/{approved.id}", headers=auth_headers)
    assert r.status_code == 400, r.text

    pending = _seed_leave_request(db, company_id, emp.id, "Annual Leave",
                                   f"{year}-04-01", f"{year}-04-03", 3, status="pending")
    r2 = client.delete(f"/api/v1/leave/requests/{pending.id}", headers=auth_headers)
    assert r2.status_code == 204, r2.text
