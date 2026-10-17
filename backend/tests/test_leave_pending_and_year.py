"""Leave balance rules found in the 2026-10-15 review:

1. Pending requests are on hold: a new request (ESS or HRMS) must fit in
   entitlement - approved - pending, and the balances show the pending days.
2. ESS counts days like HRMS: a Working-basis annual policy skips weekends.
3. A request is checked against the year it starts in, not the current year."""
import pytest
import uuid
from datetime import date, timedelta

from app.models import Employee, LeaveRequest
from tests.test_ess_requests import _company_id


def _uid():
    return uuid.uuid4().hex[:6]


def _employee_with_login(client, db, auth_headers, cid, leave_policy=None):
    no = f"LV-{_uid()}"
    rec = {"id": no, "employee_no": no, "name": f"{no} Staff", "department": "Ops", "status": "Active"}
    if leave_policy:
        rec["leave_policy"] = leave_policy
    r = client.post("/api/v1/app-data?action=save", headers=auth_headers, json={"collection": "employees", "record": rec})
    assert r.status_code == 200, r.text
    emp = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).one()
    user = f"{no.lower()}.u"
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers,
                   json={"username": user, "password": "secret123", "is_active": True})
    assert r.status_code == 200, r.text
    token = client.post("/api/v1/ess/login", json={"username": user, "password": "secret123"}).json()["access_token"]
    return emp, {"Authorization": f"Bearer {token}"}


def _approved(db, cid, emp, start, days, leave_type="Annual Leave"):
    end = date.fromisoformat(start) + timedelta(days=days - 1)
    db.add(LeaveRequest(company_id=cid, employee_id=emp.id, leave_type=leave_type, start_date=start,
                        end_date=end.isoformat(), days=days, status="approved"))
    db.commit()


def _ess_leave(client, h, start, end, leave_type="Annual Leave"):
    return client.post("/api/v1/ess/leave", headers=h, json={"leave_type": leave_type, "start_date": start, "end_date": end})


def _this_year_window():
    """Two non-overlapping 10-day windows starting soon, inside this calendar year when possible."""
    first = date.today() + timedelta(days=2)
    if (date(first.year, 12, 31) - first).days < 40:
        first = date(first.year + 1, 2, 1)   # too close to year end: use next year for every date
    return first


def test_pending_requests_hold_days_in_ess(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _employee_with_login(client, db, auth_headers, cid)
    s1 = _this_year_window()
    s2 = s1 + timedelta(days=15)
    # default entitlement 21: 10 used, 10 pending -> 1 left
    _approved(db, cid, emp, (s1 + timedelta(days=30)).isoformat(), 10)
    r = _ess_leave(client, h, s1.isoformat(), (s1 + timedelta(days=9)).isoformat())
    assert r.status_code == 201, r.text
    r = _ess_leave(client, h, s2.isoformat(), (s2 + timedelta(days=4)).isoformat())
    assert r.status_code == 400 and "on hold" in r.json()["detail"], r.text
    r = _ess_leave(client, h, s2.isoformat(), s2.isoformat())
    assert r.status_code == 201, r.text   # exactly the 1 day left still fits

    if s1.year == date.today().year:
        bal = client.get("/api/v1/ess/leave-balance", headers=h).json()["by_type"]["Annual Leave"]
        assert (bal["entitlement"], bal["used"], bal["pending"], bal["remaining"]) == (21, 10, 11, 0)


def test_hrms_create_also_counts_pending_and_balance_shows_it(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _employee_with_login(client, db, auth_headers, cid)
    s1 = _this_year_window()
    assert _ess_leave(client, h, s1.isoformat(), (s1 + timedelta(days=19)).isoformat()).status_code == 201   # 20 pending
    s2 = s1 + timedelta(days=25)
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave", "start_date": s2.isoformat(), "end_date": (s2 + timedelta(days=1)).isoformat()})
    assert r.status_code == 400, r.text
    # Unpaid leave isn't capped, so HR can still record it
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Unpaid Leave", "start_date": s2.isoformat(), "end_date": (s2 + timedelta(days=1)).isoformat()})
    assert r.status_code == 201, r.text

    if s1.year == date.today().year:
        row = next(b for b in client.get("/api/v1/leave/balance", headers=auth_headers).json() if b["employee_id"] == emp.id)
        assert (row["pending"], row["remaining"], row["available"]) == (20, 21, 1)


def test_ess_counts_working_days_like_hrms(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    # a built-in Working-basis policy (saving HR Settings here would change it for other tests' company)
    emp, h = _employee_with_login(client, db, auth_headers, cid, leave_policy="Internal 22 Working")
    monday = date.today() + timedelta(days=7 - date.today().weekday() + 7)
    sunday = monday + timedelta(days=6)

    r = _ess_leave(client, h, monday.isoformat(), sunday.isoformat())
    assert r.status_code == 201 and r.json()["days"] == 5, r.text
    assert client.post(f"/api/v1/ess/leave/{r.json()['id']}/cancel", headers=h).status_code == 200
    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave", "start_date": monday.isoformat(), "end_date": sunday.isoformat()})
    assert r.status_code == 201 and r.json()["days"] == 5, r.text
    assert client.get("/api/v1/ess/leave-balance", headers=h).json()["by_type"]["Annual Leave"]["basis"] == "Working"


def test_next_years_leave_uses_next_years_balance(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _employee_with_login(client, db, auth_headers, cid)
    year = date.today().year
    _approved(db, cid, emp, f"{year}-01-05", 20)            # this year: 1 day left
    start, end = f"{year + 1}-01-12", f"{year + 1}-01-16"     # next year: untouched

    r = _ess_leave(client, h, start, end)
    assert r.status_code == 201, r.text
    assert client.post(f"/api/v1/ess/leave/{r.json()['id']}/cancel", headers=h).status_code == 200

    r = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave", "start_date": start, "end_date": end})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/leave/requests/{r.json()['id']}/approve", headers=auth_headers)
    assert r.status_code == 200, r.text

    # next year now has 16 days left, and new requests are checked against that
    r = _ess_leave(client, h, f"{year + 1}-03-02", f"{year + 1}-03-31")
    assert r.status_code == 400, r.text   # 30 days > next year's 16 left


def test_approval_still_checks_entitlement_for_the_leave_year(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, _h = _employee_with_login(client, db, auth_headers, cid)
    year = date.today().year + 1
    _approved(db, cid, emp, f"{year}-02-02", 20)
    db.add(LeaveRequest(company_id=cid, employee_id=emp.id, leave_type="Annual Leave", start_date=f"{year}-03-02",
                        end_date=f"{year}-03-06", days=5, status="pending"))   # filed before the 20 were approved
    db.commit()
    pending = db.query(LeaveRequest).filter(LeaveRequest.employee_id == emp.id, LeaveRequest.status == "pending").one()
    r = client.post(f"/api/v1/leave/requests/{pending.id}/approve", headers=auth_headers)
    assert r.status_code == 409, r.text


@pytest.fixture(autouse=True)
def _full_year_entitlement(monkeypatch):
    """These tests are about other rules: give the whole year's Annual Leave up front, as
    before accrual (tests/test_leave_accrual.py covers accrual)."""
    from app.routers import leave as _leave
    monkeypatch.setattr(_leave, "accrued_days", lambda settings, yearly, join_date, on: yearly)
