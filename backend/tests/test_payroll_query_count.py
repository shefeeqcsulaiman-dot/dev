"""Payroll generate reads each app-data list (overtime, loans, advances, adjustments)
once per request, not once per employee: its query count must not grow with
headcount. Counted from the Server-Timing header (app/monitoring.py)."""
import json

from app.models import AppDataRecord, Employee
from tests.conftest import ensure_user


def _company_headers(client, db, email, trn):
    user = ensure_user(db, email, trn)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"})
    assert r.status_code == 200, r.text
    return user.company_id, {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed(db, company_id, employees):
    for i in range(employees):
        no = f"QC-{i:03d}"
        db.add(Employee(company_id=company_id, employee_no=no, full_name=f"Query Count {i}",
                        basic_salary=6000, status="active"))
        db.add(AppDataRecord(company_id=company_id, collection="overtimeRequests", record_key=f"OT-{no}",
                             payload=json.dumps({"employee_id": no, "status": "Approved", "date": "2025-03-10",
                                                 "ot_hours": 2, "multiplier": "1.25"})))
        db.add(AppDataRecord(company_id=company_id, collection="employeeLoans", record_key=f"LN-{no}",
                             payload=json.dumps({"employee_id": no, "status": "Approved", "amount": 1200,
                                                 "balance": 1200, "emi": 100})))
    db.commit()


def _generate_queries(client, headers):
    r = client.post("/api/v1/payroll/generate", json={"period": "2025-03"}, headers=headers)
    assert r.status_code == 201, r.text
    timing = r.headers["Server-Timing"]
    return int(timing.split('desc="')[1].split(" ")[0]), r.json()


def test_generate_query_count_does_not_grow_with_employees(client, db):
    small_co, small = _company_headers(client, db, "payroll-qc-small@taxflowqa.com", "900000000000731")
    big_co, big = _company_headers(client, db, "payroll-qc-big@taxflowqa.com", "900000000000732")
    _seed(db, small_co, 2)
    _seed(db, big_co, 40)

    small_queries, _ = _generate_queries(client, small)
    big_queries, run = _generate_queries(client, big)

    # Was ~4 queries per employee (one per list per employee); now a fixed handful.
    assert big_queries <= small_queries + 3, (small_queries, big_queries)

    # And the figures are still per employee: 2h OT at 1.25x, EMI 100 deducted.
    assert len(run["items"]) == 40
    item = run["items"][0]
    assert float(item["deductions"]) == 100.0
    assert float(item["overtime"]) > 0
