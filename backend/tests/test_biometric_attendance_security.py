"""Biometric attendance security/correctness fixes from the 2026-08-21 deep
audit: device management admin-only enforcement, the Sync Activity Log's
"matched" flag, and the device-key auth cache. See routers/attendance.py.
"""
from app.models import Employee, User
from tests.conftest import ensure_user


def _non_admin_headers(client, db, email="qa-staff@taxflowqa.com", trn="900000000000001"):
    """A second user in the SAME company (same trn) as auth_headers' admin,
    with a non-admin role — for testing that device management is
    genuinely admin-only, not just company-scoped."""
    ensure_user(db, email, trn, role="staff")
    db.commit()
    response = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_device_management_is_admin_only(client, auth_headers, db):
    staff_headers = _non_admin_headers(client, db)

    # A non-admin cannot create a device...
    denied_create = client.post("/api/v1/attendance/devices", json={
        "name": "Staff-Added Device", "device_type": "ZKTeco iClock",
        "ip_address": "192.168.1.50", "port": 4370,
    }, headers=staff_headers)
    assert denied_create.status_code == 403

    # ...but an admin still can, and a non-admin can still just VIEW the list.
    created = client.post("/api/v1/attendance/devices", json={
        "name": "Admin-Added Device", "device_type": "ZKTeco iClock",
        "ip_address": "192.168.1.51", "port": 4370,
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    device_id = created.json()["id"]

    listed = client.get("/api/v1/attendance/devices", headers=staff_headers)
    assert listed.status_code == 200
    assert any(d["id"] == device_id for d in listed.json())

    # Non-admin cannot Test, Delete, or (for a BioTime device) Sync Now.
    denied_test = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=staff_headers)
    assert denied_test.status_code == 403

    denied_delete = client.delete(f"/api/v1/attendance/devices/{device_id}", headers=staff_headers)
    assert denied_delete.status_code == 403

    # Confirm the admin's own access is genuinely unaffected by any of this.
    admin_delete = client.delete(f"/api/v1/attendance/devices/{device_id}", headers=auth_headers)
    assert admin_delete.status_code == 204


def test_sync_log_flags_unmatched_employee_id(client, auth_headers, db):
    """A punch whose employee_id doesn't match any real Employee.employee_no
    must be visibly flagged, not rendered identically to a matched punch —
    otherwise the Setup Guide's own "check if it appears but isn't linked"
    troubleshooting step has nothing for an admin to actually look at."""
    admin_user = db.query(User).filter(User.email == "qa-admin@taxflowqa.com").one()
    db.add(Employee(company_id=admin_user.company_id, employee_no="MATCHED-001", full_name="Matched Employee"))
    db.commit()

    matched_punch = client.post("/api/v1/attendance/punch", headers=auth_headers, json={
        "employee_id": "MATCHED-001", "employee_name": "Matched Employee", "direction": "in",
    })
    assert matched_punch.status_code == 201, matched_punch.text

    unmatched_punch = client.post("/api/v1/attendance/punch", headers=auth_headers, json={
        "employee_id": "NO-SUCH-EMPLOYEE-ID", "employee_name": "Ghost Punch", "direction": "in",
    })
    assert unmatched_punch.status_code == 201, unmatched_punch.text

    log = client.get("/api/v1/attendance/punches?limit=50", headers=auth_headers)
    assert log.status_code == 200
    punches = {p["employee_id"]: p for p in log.json()["punches"]}
    assert punches["MATCHED-001"]["matched"] is True
    assert punches["NO-SUCH-EMPLOYEE-ID"]["matched"] is False


def test_device_key_lookup_is_cached_after_first_match(client, auth_headers, monkeypatch):
    """_get_device_company() previously bcrypt-verified against every active
    device on every single request, even for a device that had already been
    successfully authenticated moments ago — a cheap short-TTL cache should
    short-circuit the repeat lookup."""
    from app.security import verify_password as real_verify_password
    import app.routers.attendance as attendance_module

    # Tests run without Redis, where app.cache.get/set are no-ops by design
    # (see cache.py's module docstring) — swap in a trivial in-memory stand-in
    # so this test actually exercises the cache-hit code path in
    # _get_device_company(), not just the always-a-miss fallback.
    fake_store: dict[str, str] = {}
    monkeypatch.setattr(attendance_module.cache, "get", lambda key: fake_store.get(key))
    monkeypatch.setattr(attendance_module.cache, "set", lambda key, value, ttl=60: fake_store.__setitem__(key, value))

    created = client.post("/api/v1/attendance/devices", json={
        "name": "Cache Test Device", "device_type": "ZKTeco iClock",
        "ip_address": "192.168.1.60", "port": 4370,
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    api_key = created.json()["api_key"]

    call_count = {"n": 0}

    def counting_verify(plain, hashed):
        call_count["n"] += 1
        return real_verify_password(plain, hashed)

    monkeypatch.setattr(attendance_module, "verify_password", counting_verify)

    first = client.post(
        "/api/v1/attendance/punch",
        headers={"X-Device-Key": api_key},
        json={"employee_id": "EMP-CACHE-001", "direction": "in"},
    )
    assert first.status_code == 201, first.text
    # auth_headers' company is shared across the whole test session (same
    # email/trn every file resolves to), so other tests' leftover devices
    # can already be sitting in the bcrypt-scan pool here — the exact count
    # for this first (genuinely-a-miss) request depends on how many of
    # those come before ours and isn't itself what's under test.
    calls_for_first_request = call_count["n"]
    assert calls_for_first_request >= 1

    second = client.post(
        "/api/v1/attendance/punch",
        headers={"X-Device-Key": api_key},
        json={"employee_id": "EMP-CACHE-001", "direction": "out"},
    )
    assert second.status_code == 201, second.text
    # Second request must be served from cache — no additional bcrypt verify.
    assert call_count["n"] == calls_for_first_request
