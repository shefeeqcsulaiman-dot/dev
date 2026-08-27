"""Regression coverage for POST /attendance/import-csv. Previously this
endpoint built and inserted AttendancePunch rows directly, bypassing every
check the device/manual ingestion paths (_ingest_device_punch) go through:
a future-dated row imported without complaint, a 90+ day old row imported
without complaint, and — since CSV rows never carry a device_id and a NULL
device_id never collides under the DB's unique constraint — re-uploading
the exact same file duplicated every punch in it forever."""
from datetime import datetime, timedelta, timezone

from app.models import Employee


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def _seed_employee(db, company_id, employee_no="CSV-TEST-001"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name="CSV Import Test Employee",
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _upload(client, auth_headers, csv_text):
    return client.post(
        "/api/v1/attendance/import-csv",
        headers=auth_headers,
        files={"file": ("attendance.csv", csv_text.encode(), "text/csv")},
    )


def test_reuploading_the_same_csv_does_not_duplicate_punches(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id)
    ts = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    csv_text = f"employee_id,employee_name,punch_time,direction\n{emp.employee_no},{emp.full_name},{ts},in\n"

    r1 = _upload(client, auth_headers, csv_text)
    assert r1.status_code == 201, r1.text
    assert r1.json()["imported"] == 1
    assert r1.json().get("duplicates", 0) == 0

    r2 = _upload(client, auth_headers, csv_text)
    assert r2.status_code == 201, r2.text
    assert r2.json()["imported"] == 0
    assert r2.json()["duplicates"] == 1


def test_future_dated_csv_row_is_rejected(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, employee_no="CSV-TEST-FUTURE")
    future_ts = (datetime.now(timezone.utc) + timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
    csv_text = f"employee_id,employee_name,punch_time,direction\n{emp.employee_no},{emp.full_name},{future_ts},in\n"

    r = _upload(client, auth_headers, csv_text)
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 0
    assert r.json()["rejected"] == 1


def test_stale_csv_row_older_than_90_days_is_rejected(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, employee_no="CSV-TEST-STALE")
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).strftime("%Y-%m-%d %H:%M:%S")
    csv_text = f"employee_id,employee_name,punch_time,direction\n{emp.employee_no},{emp.full_name},{old_ts},in\n"

    r = _upload(client, auth_headers, csv_text)
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 0
    assert r.json()["rejected"] == 1
