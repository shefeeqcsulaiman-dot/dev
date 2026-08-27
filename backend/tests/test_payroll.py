"""Regression coverage for POST /payroll/generate. Previously this endpoint
read employee.allowances/.overtime/.deductions, none of which exist on the
Employee model (they're PayrollItem-only fields) — every call raised an
uncaught AttributeError and returned 500, for every company, always. Found
via a real production run generating payroll for 100 seeded employees."""
import json

from app.models import AppDataRecord, Branch, Employee


def _seed_active_employee(db, company_id, basic_salary=8000, employee_no="PR-TEST-001",
                           full_name="Payroll Test Employee", **extra):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=basic_salary, status="active", **extra)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _seed_app_record(db, company_id, collection, record_key, payload):
    row = AppDataRecord(company_id=company_id, collection=collection, record_key=record_key,
                         payload=json.dumps(payload))
    db.add(row)
    db.commit()
    return row


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


def test_generate_payroll_sums_allowance_columns(client, db, auth_headers):
    # Housing/Transport/Other allowance columns previously didn't exist at
    # all — payroll.py hardcoded allowances to 0.00 no matter what.
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = _seed_active_employee(
        db, company_id, basic_salary=8000, employee_no="PR-TEST-ALLOW",
        housing_allowance="1000.00", transport_allowance="500.00", other_allowance="200.00",
    )

    r = client.post("/api/v1/payroll/generate", json={"period": "2025-09"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    item = next(i for i in r.json()["items"] if i["employee_id"] == emp.id)
    assert item["allowances"] == "1700.00"
    assert item["net_pay"] == "9700.00"


def test_generate_payroll_deducts_approved_loan_and_reduces_stored_balance(client, db, auth_headers):
    # Regression for the loan-approval full-payload-overwrite bug: as long as
    # the saved employeeLoans record still carries employee_id/balance/emi
    # (the fields the frontend fix now preserves through approve/reject),
    # payroll must actually deduct the EMI and write the reduced balance back.
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = _seed_active_employee(db, company_id, basic_salary=8000, employee_no="PR-TEST-LOAN")
    _seed_app_record(db, company_id, "employeeLoans", "LOAN-1", {
        "id": "LOAN-1", "employee_id": emp.employee_no, "employee": emp.full_name,
        "status": "Approved", "balance": "1500.00", "emi": "500.00",
    })

    r = client.post("/api/v1/payroll/generate", json={"period": "2025-12"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    item = next(i for i in r.json()["items"] if i["employee_id"] == emp.id)
    assert item["deductions"] == "500.00"
    assert item["net_pay"] == "7500.00"

    row = (
        db.query(AppDataRecord)
        .filter_by(company_id=company_id, collection="employeeLoans", record_key="LOAN-1")
        .order_by(AppDataRecord.created_at.desc())
        .first()
    )
    stored = json.loads(row.payload)
    assert stored["balance"] == "1000.00"
    assert stored["status"] == "Approved"


def test_generate_payroll_uses_configured_ot_hours_per_month(client, db, auth_headers):
    # Previously hardcoded to a 240-hour month regardless of what HR
    # Settings > OT Rules actually advertised on screen (22 work days x 10
    # work hours = 220 here, not the 176 or 240 defaults).
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = _seed_active_employee(db, company_id, basic_salary=8800, employee_no="PR-TEST-OT")
    _seed_app_record(db, company_id, "hr_settings", "ot-rules-config", {
        "id": "ot-rules-config", "workDays": "22", "workHours": "10",
    })
    _seed_app_record(db, company_id, "overtimeRequests", "OT-1", {
        "id": "OT-1", "employee_id": emp.employee_no, "employee": emp.full_name,
        "status": "Approved", "date": "2026-01-10", "ot_hours": "10", "multiplier": "1.25",
    })

    r = client.post("/api/v1/payroll/generate", json={"period": "2026-01"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    item = next(i for i in r.json()["items"] if i["employee_id"] == emp.id)
    # hourly rate = 8800 / 220 = 40.00; OT = 10 hrs x 40.00 x 1.25 = 500.00
    assert item["overtime"] == "500.00"


def test_generate_payroll_rejects_branch_run_overlapping_company_wide_run(client, db, auth_headers):
    # A branch-scoped run and a company-wide run (branch_id=None) for the
    # same period previously didn't collide at all (different branch_id
    # keys), so the same employee could be paid twice for one period.
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    branch = Branch(company_id=company_id, name="Payroll Guard Branch")
    db.add(branch)
    db.commit()
    _seed_active_employee(db, company_id, basic_salary=6000, employee_no="PR-TEST-GUARD")

    r1 = client.post("/api/v1/payroll/generate", json={"period": "2025-10"}, headers=auth_headers)
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        "/api/v1/payroll/generate", json={"period": "2025-10", "branch_id": branch.id}, headers=auth_headers
    )
    assert r2.status_code == 409, r2.text
