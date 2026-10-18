"""Carried-forward Annual Leave (2026-10-17): days HR enters on the employee are added to the
leave year they were entered in, capped by the Annual Leave type's Max Carry, and usable until
the Carry-Forward Deadline; after it only the carried days already used still count."""
import uuid
from datetime import date

import pytest

from app.models import Employee
from app.routers.leave import carry_deadline, carry_forward_days

YEAR = (date(2026, 1, 1), date(2027, 1, 1))


@pytest.fixture(autouse=True)
def _full_year_entitlement(monkeypatch):
    from app.routers import leave as _leave
    monkeypatch.setattr(_leave, "accrued_days", lambda settings, yearly, join_date, on: yearly)


def test_deadline_from_settings():
    assert carry_deadline({}, *YEAR) == date(2026, 3, 31)
    assert carry_deadline({"carry_deadline": "End of January"}, *YEAR) == date(2026, 1, 31)
    assert carry_deadline({"carry_deadline": "End of Leave Year"}, *YEAR) == date(2026, 12, 31)
    assert carry_deadline({"carry_deadline": "No deadline"}, *YEAR) is None
    assert carry_deadline({"carry_deadline": "End of March"}, date(2026, 4, 1), date(2027, 4, 1)) == date(2027, 3, 31)


def test_carry_counts_until_the_deadline_then_only_what_was_used():
    prof = {"carry": 8, "carry_entered": date(2026, 1, 10)}
    assert carry_forward_days({}, prof, YEAR, date(2026, 2, 1)) == (8, 8, date(2026, 3, 31))
    assert carry_forward_days({}, prof, YEAR, date(2026, 5, 1), used_before_deadline=3) == (3, 8, date(2026, 3, 31))
    capped = {"leave_types": [{"type": "Annual Leave", "max_carry": "5"}]}
    assert carry_forward_days(capped, prof, YEAR, date(2026, 2, 1))[1] == 5
    # entered after the deadline (starting mid-year): usable to the end of the leave year
    late = {"carry": 4, "carry_entered": date(2026, 10, 17)}
    assert carry_forward_days({}, late, YEAR, date(2026, 11, 1)) == (4, 4, date(2026, 12, 31))
    # entered last year: not this year's carry
    assert carry_forward_days({}, {"carry": 8, "carry_entered": date(2025, 6, 1)}, YEAR, date(2026, 2, 1)) == (0, 0, None)


def test_balance_includes_carried_days(client, db, auth_headers):
    cid = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    no = f"CF{uuid.uuid4().hex[:6]}"
    today = date.today()
    client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "hrLeavePolicy", "record": {
        "id": "leave-policy", "carry_deadline": "End of Leave Year", "leave_types": [{"type": "Annual Leave", "days": 21, "max_carry": "10"}]}})
    try:
        r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "employees", "record": {
            "id": no, "name": f"Carry {no}", "department": "CarryDept", "designation": "Staff", "status": "Active", "salary": 4000,
            "leave_carry_forward": "12", "leave_carry_entered": today.isoformat()}})
        assert r.status_code == 200, r.text
        db.expire_all()
        emp = db.query(Employee).filter(Employee.company_id == cid, Employee.employee_no == no).first()
        bal = next(b for b in client.get("/api/v1/leave/balance", headers=auth_headers).json() if b["employee_id"] == emp.id)
        assert bal["carried_forward"]["days"] == 10                  # capped by Max Carry
        assert bal["annual_entitlement"] == 21 + 10
    finally:
        client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "hrLeavePolicy", "record": {"id": "leave-policy"}})
