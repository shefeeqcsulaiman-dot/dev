"""Regression coverage for DELETE /attendance/punches/{id}. Previously there
was no way to remove a single erroneous punch (a mis-scanned device event,
a typo'd employee_id, a diagnostic/test punch) short of a superadmin wiping
a company's entire attendance history."""
from datetime import datetime, timezone

from app.models import AttendancePunch


def _company_id(client, auth_headers):
    return client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]


def test_delete_punch_removes_it(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    punch = AttendancePunch(
        company_id=company_id, employee_id="__diag_test__", employee_name="Diagnostic Test",
        punch_time=datetime.now(timezone.utc), punch_date="2026-08-28", direction="in", source="device",
    )
    db.add(punch)
    db.commit()
    db.refresh(punch)

    r = client.delete(f"/api/v1/attendance/punches/{punch.id}", headers=auth_headers)
    assert r.status_code == 204, r.text

    remaining = db.query(AttendancePunch).filter(AttendancePunch.id == punch.id).first()
    assert remaining is None


def test_delete_punch_404_for_unknown_id(client, auth_headers):
    r = client.delete("/api/v1/attendance/punches/does-not-exist", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_delete_punch_scoped_to_own_company(client, db, auth_headers, second_tenant_headers):
    company_id = _company_id(client, auth_headers)
    punch = AttendancePunch(
        company_id=company_id, employee_id="__diag_test__2", employee_name=None,
        punch_time=datetime.now(timezone.utc), punch_date="2026-08-28", direction="in", source="device",
    )
    db.add(punch)
    db.commit()
    db.refresh(punch)

    # A different company's admin must not be able to delete this punch.
    r = client.delete(f"/api/v1/attendance/punches/{punch.id}", headers=second_tenant_headers)
    assert r.status_code == 404, r.text

    still_there = db.query(AttendancePunch).filter(AttendancePunch.id == punch.id).first()
    assert still_there is not None
