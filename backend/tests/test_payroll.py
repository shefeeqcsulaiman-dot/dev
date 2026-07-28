"""Regression coverage for POST /payroll/generate. Previously this endpoint
read employee.allowances/.overtime/.deductions, none of which exist on the
Employee model (they're PayrollItem-only fields) — every call raised an
uncaught AttributeError and returned 500, for every company, always. Found
via a real production run generating payroll for 100 seeded employees."""
from app.models import Employee


def _seed_active_employee(db, company_id, basic_salary=8000):
    emp = Employee(company_id=company_id, employee_no="PR-TEST-001", full_name="Payroll Test Employee",
                    basic_salary=basic_salary, status="active")
    db.add(emp)
    db.commit()
    return emp


def test_generate_payroll_succeeds_for_active_employees(client, db, auth_headers):
    # auth_headers reuses the same tenant across the whole test session, so
    # other tests' leftover employees may already be active in this company
    # — assert on this test's own employee/item, not run-wide totals/counts.
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = _seed_active_employee(db, company_id, basic_salary=8000)

    r = client.post("/api/v1/payroll/generate", json={"period": "2025-07"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    run = r.json()
    item = next(i for i in run["items"] if i["employee_id"] == emp.id)
    assert item["basic"] == "8000.00"
    assert item["net_pay"] == "8000.00"
    assert item["allowances"] == "0.00"


def test_generate_payroll_rejects_duplicate_period(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    _seed_active_employee(db, company_id)

    r1 = client.post("/api/v1/payroll/generate", json={"period": "2025-08"}, headers=auth_headers)
    assert r1.status_code == 201
    r2 = client.post("/api/v1/payroll/generate", json={"period": "2025-08"}, headers=auth_headers)
    assert r2.status_code == 409
