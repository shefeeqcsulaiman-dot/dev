"""BioTime server integration: connecting a per-company BioTime 9.5 install
(customer-hosted device management software) and pulling attendance from it.
See biotime_client.py / biotime_sync.py / routers/attendance.py.

biotime_client's network calls are monkeypatched throughout — no real HTTP
call ever leaves this test.
"""
from datetime import UTC, datetime, timedelta

from app import biotime_client, crypto
from app.models import AttendancePunch, BiometricDevice


def _add_biotime_device(client, headers, name="Main BioTime"):
    resp = client.post("/api/v1/attendance/devices", json={
        "name": name,
        "device_type": "ZKTeco BioTime Server",
        "biotime_base_url": "http://biotime.example.com:8098",
        "biotime_username": "bio_admin",
        "biotime_password": "SuperSecret123",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_add_biotime_device_requires_credentials(client, auth_headers):
    resp = client.post("/api/v1/attendance/devices", json={
        "name": "Incomplete",
        "device_type": "ZKTeco BioTime Server",
        "biotime_base_url": "http://biotime.example.com:8098",
    }, headers=auth_headers)
    assert resp.status_code == 400


def test_add_biotime_device_encrypts_password_at_rest(client, auth_headers, db):
    device_id = _add_biotime_device(client, auth_headers)
    device = db.query(BiometricDevice).filter(BiometricDevice.id == device_id).first()
    assert device is not None
    assert device.biotime_password_enc is not None
    assert "SuperSecret123" not in device.biotime_password_enc
    assert crypto.decrypt_secret(device.biotime_password_enc) == "SuperSecret123"


def test_add_biotime_device_never_returns_password(client, auth_headers):
    device_id = _add_biotime_device(client, auth_headers)
    resp = client.get("/api/v1/attendance/devices", headers=auth_headers)
    assert resp.status_code == 200
    devices = {d["id"]: d for d in resp.json()}
    assert "biotime_password" not in devices[device_id]
    assert "biotime_password_enc" not in devices[device_id]
    assert devices[device_id]["biotime_username"] == "bio_admin"


def test_test_device_returns_mocked_terminal_count(client, auth_headers, monkeypatch):
    device_id = _add_biotime_device(client, auth_headers)
    monkeypatch.setattr(biotime_client, "get_token", lambda base_url, username, password: "fake-token")
    monkeypatch.setattr(biotime_client, "list_terminals", lambda base_url, token: [
        {"alias": "Main Gate", "sn": "SN001"},
        {"alias": "Back Door", "sn": "SN002"},
    ])
    resp = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "2 terminal" in body["message"]


def test_test_device_surfaces_connection_error(client, auth_headers, monkeypatch):
    device_id = _add_biotime_device(client, auth_headers)

    def raise_error(base_url, username, password):
        raise biotime_client.BioTimeError("Could not reach BioTime server at http://biotime.example.com:8098: timeout")
    monkeypatch.setattr(biotime_client, "get_token", raise_error)

    resp = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "biotime.example.com" in body["message"]


def test_sync_inserts_punches_scoped_to_company(client, auth_headers, db, monkeypatch):
    device_id = _add_biotime_device(client, auth_headers)
    monkeypatch.setattr(biotime_client, "get_token", lambda base_url, username, password: "fake-token")

    punch_time = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(biotime_client, "list_transactions", lambda base_url, token, start_time, end_time: [
        {"emp_code": "EMP001", "punch_time": punch_time, "punch_state": "0", "terminal_alias": "Main Gate"},
    ])

    resp = client.post(f"/api/v1/attendance/devices/{device_id}/biotime/sync", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["synced"] == 1

    punches = db.query(AttendancePunch).filter(AttendancePunch.device_id == device_id).all()
    assert len(punches) == 1
    assert punches[0].employee_id == "EMP001"
    assert punches[0].direction == "in"
    assert punches[0].source == "biotime"

    device = db.query(BiometricDevice).filter(BiometricDevice.id == device_id).first()
    assert punches[0].company_id == device.company_id


def test_sync_is_idempotent_across_repeated_runs(client, auth_headers, db, monkeypatch):
    device_id = _add_biotime_device(client, auth_headers)
    monkeypatch.setattr(biotime_client, "get_token", lambda base_url, username, password: "fake-token")

    punch_time = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    monkeypatch.setattr(biotime_client, "list_transactions", lambda base_url, token, start_time, end_time: [
        {"emp_code": "EMP001", "punch_time": punch_time, "punch_state": "0", "terminal_alias": "Main Gate"},
    ])

    first = client.post(f"/api/v1/attendance/devices/{device_id}/biotime/sync", headers=auth_headers)
    assert first.json()["synced"] == 1

    # Same transaction returned again (overlapping window) — must not duplicate.
    second = client.post(f"/api/v1/attendance/devices/{device_id}/biotime/sync", headers=auth_headers)
    assert second.json()["synced"] == 0

    punches = db.query(AttendancePunch).filter(AttendancePunch.device_id == device_id).all()
    assert len(punches) == 1


def test_cross_tenant_cannot_sync_test_or_delete_others_device(client, auth_headers, second_tenant_headers):
    device_id = _add_biotime_device(client, auth_headers)

    resp = client.post(f"/api/v1/attendance/devices/{device_id}/biotime/sync", headers=second_tenant_headers)
    assert resp.status_code == 404

    resp2 = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=second_tenant_headers)
    assert resp2.status_code == 404

    resp3 = client.delete(f"/api/v1/attendance/devices/{device_id}", headers=second_tenant_headers)
    assert resp3.status_code == 404

    # Confirm it's genuinely untouched from company A's own view.
    still_there = client.get("/api/v1/attendance/devices", headers=auth_headers)
    assert any(d["id"] == device_id for d in still_there.json())


def test_sync_uses_saudi_offset_not_hardcoded_uae(client, auth_headers, db, monkeypatch):
    """The UTC offset used to be a single hardcoded UAE+4 constant — silently
    wrong for any other GCC company. A Saudi Arabia company (UTC+3) synced
    punch must convert using a 3-hour offset, not 4."""
    updated = client.put("/api/v1/companies/current", headers=auth_headers, json={"country": "Saudi Arabia"})
    assert updated.status_code == 200, updated.text

    device_id = _add_biotime_device(client, auth_headers, name="Riyadh Gate")
    monkeypatch.setattr(biotime_client, "get_token", lambda base_url, username, password: "fake-token")
    # 09:00 local time in Riyadh (UTC+3) is 06:00 UTC — if the code still used
    # the old hardcoded UAE+4 offset this would wrongly compute 05:00 UTC.
    local_punch_str = "2026-01-15 09:00:00"
    monkeypatch.setattr(biotime_client, "list_transactions", lambda base_url, token, start_time, end_time: [
        {"emp_code": "EMP-SA-001", "punch_time": local_punch_str, "punch_state": "0", "terminal_alias": "Riyadh Gate"},
    ])

    resp = client.post(f"/api/v1/attendance/devices/{device_id}/biotime/sync", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["synced"] == 1

    punch = db.query(AttendancePunch).filter(AttendancePunch.device_id == device_id).one()
    # SQLite round-trips DateTime(timezone=True) as naive (see biotime_sync.py's
    # own _key() comment for the same quirk) — compare the naive readback against
    # a naive literal rather than a tz-aware one.
    assert punch.punch_time == datetime(2026, 1, 15, 6, 0, 0)
    assert punch.punch_date == "2026-01-15"


def test_attendance_punch_dedup_constraint_rejects_true_duplicate(db, auth_headers, client):
    """Direct model-level check that uq_attendance_punch_dedup (models.py)
    actually exists and rejects a true duplicate — the backstop the
    application-level SELECT-then-INSERT dedupe relies on under
    concurrency. Resolve a real company_id via the API rather than
    hardcoding one, matching this file's existing fixture style."""
    from sqlalchemy.exc import IntegrityError

    company = client.get("/api/v1/companies/current", headers=auth_headers).json()
    punch_time = datetime(2026, 2, 1, 8, 0, 0, tzinfo=UTC)
    first = AttendancePunch(
        company_id=company["id"], employee_id="EMP-DEDUP-001", punch_time=punch_time,
        punch_date="2026-02-01", direction="in", device_id="dedup-test-device", source="device",
    )
    db.add(first)
    db.commit()

    dup = AttendancePunch(
        company_id=company["id"], employee_id="EMP-DEDUP-001", punch_time=punch_time,
        punch_date="2026-02-01", direction="in", device_id="dedup-test-device", source="device",
    )
    db.add(dup)
    try:
        db.commit()
        assert False, "expected IntegrityError from uq_attendance_punch_dedup"
    except IntegrityError:
        db.rollback()

    assert db.query(AttendancePunch).filter(
        AttendancePunch.company_id == company["id"], AttendancePunch.employee_id == "EMP-DEDUP-001"
    ).count() == 1
