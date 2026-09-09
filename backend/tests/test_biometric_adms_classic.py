"""ZKTeco ADMS Classic — the real iClock wire protocol for devices whose
own menu only has a fixed Server IP + Port field (no custom URL/header, so
the existing X-Device-Key webhook path, tested in
test_biometric_attendance_security.py, doesn't apply). See attendance.py's
iclock_router and _get_device_by_serial()."""
import json
from datetime import UTC, datetime, timedelta

from app.models import AttendanceDetail, Employee


def _add_adms_classic_device(client, auth_headers, serial="SN-TEST-0001", name="Front Door Scanner"):
    return client.post("/api/v1/attendance/devices", json={
        "name": name, "device_type": "ZKTeco ADMS Classic", "serial_number": serial,
    }, headers=auth_headers)


def _device_local_ts(hours_ago=2):
    """A device-local wall-clock timestamp string that lands safely in the
    past once the endpoint applies the default UAE+4 offset (naive - 4h),
    regardless of what real time the test suite happens to run at — a fixed
    hardcoded date/time would intermittently trip the "punch is in the
    future" rejection depending on the clock at run time."""
    local = datetime.now(UTC) + timedelta(hours=4) - timedelta(hours=hours_ago)
    return local.strftime("%Y-%m-%d %H:%M:%S")


def test_add_device_requires_serial_and_issues_no_api_key(client, auth_headers):
    missing_serial = _add_adms_classic_device(client, auth_headers, serial="")
    assert missing_serial.status_code == 400

    created = _add_adms_classic_device(client, auth_headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["serial_number"] == "SN-TEST-0001"
    # Unlike the bearer-key device types, real ADMS never gets an api_key —
    # it identifies itself by serial number in the URL instead.
    assert "api_key" not in body


def test_duplicate_serial_number_rejected(client, auth_headers):
    first = _add_adms_classic_device(client, auth_headers, serial="SN-DUPLICATE")
    assert first.status_code == 201, first.text

    dupe = _add_adms_classic_device(client, auth_headers, serial="SN-DUPLICATE", name="Second Scanner")
    assert dupe.status_code == 409


def test_handshake_unknown_serial_rejected(client):
    resp = client.get("/iclock/cdata", params={"SN": "SN-DOES-NOT-EXIST"})
    assert resp.status_code == 404


def test_handshake_known_serial_returns_config(client, auth_headers):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-HANDSHAKE-001")
    assert created.status_code == 201, created.text

    resp = client.get("/iclock/cdata", params={"SN": "SN-HANDSHAKE-001"})
    assert resp.status_code == 200
    assert "Stamp=" in resp.text
    assert "SN-HANDSHAKE-001" in resp.text


def test_getrequest_ok_for_known_device_404_for_unknown(client, auth_headers):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-GETREQ-001")
    assert created.status_code == 201, created.text

    ok = client.get("/iclock/getrequest", params={"SN": "SN-GETREQ-001"})
    assert ok.status_code == 200
    assert ok.text.strip() == "OK"

    unknown = client.get("/iclock/getrequest", params={"SN": "SN-NOPE"})
    assert unknown.status_code == 404


def test_attlog_upload_creates_punches(client, auth_headers, db):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-ATTLOG-001")
    assert created.status_code == 201, created.text

    body = "\n".join([
        f"ATTLOG-EMP-1\t{_device_local_ts(2)}\t0\t1",
        f"ATTLOG-EMP-2\t{_device_local_ts(1)}\t1\t1",
    ])
    resp = client.post(
        "/iclock/cdata",
        params={"SN": "SN-ATTLOG-001", "table": "ATTLOG"},
        content=body,
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "OK: 2"

    rows = db.query(AttendanceDetail).filter(
        AttendanceDetail.employee_id.in_(["ATTLOG-EMP-1", "ATTLOG-EMP-2"])
    ).all()
    assert len(rows) == 2
    by_id = {r.employee_id: json.loads(r.raw_events)[0] for r in rows}
    assert by_id["ATTLOG-EMP-1"]["direction"] == "in"
    assert by_id["ATTLOG-EMP-2"]["direction"] == "out"
    assert all(e["source"] == "device" for e in by_id.values())


def test_attlog_upload_dedupes_repeated_lines(client, auth_headers, db):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-DEDUPE-001")
    assert created.status_code == 201, created.text

    line = f"DEDUPE-EMP-1\t{_device_local_ts(2)}\t0\t1"
    first = client.post(
        "/iclock/cdata", params={"SN": "SN-DEDUPE-001", "table": "ATTLOG"},
        content=line, headers={"Content-Type": "text/plain"},
    )
    assert first.status_code == 200
    second = client.post(
        "/iclock/cdata", params={"SN": "SN-DEDUPE-001", "table": "ATTLOG"},
        content=line, headers={"Content-Type": "text/plain"},
    )
    assert second.status_code == 200

    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "DEDUPE-EMP-1").one()
    assert len(json.loads(row.raw_events)) == 1


def test_attlog_upload_matches_employee_by_employee_no(client, auth_headers, db):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-MATCH-001")
    assert created.status_code == 201, created.text
    from app.models import User
    admin_user = db.query(User).filter(User.email == "qa-admin@taxflowqa.com").one()
    db.add(Employee(company_id=admin_user.company_id, employee_no="MATCH-EMP-01", full_name="Matched Employee"))
    db.commit()

    line = f"MATCH-EMP-01\t{_device_local_ts(2)}\t0\t1"
    resp = client.post(
        "/iclock/cdata", params={"SN": "SN-MATCH-001", "table": "ATTLOG"},
        content=line, headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200

    log = client.get("/api/v1/attendance/punches?limit=50", headers=auth_headers)
    assert log.status_code == 200
    punches = {p["employee_id"]: p for p in log.json()["punches"]}
    assert punches["MATCH-EMP-01"]["matched"] is True


def test_attlog_upload_ignores_malformed_lines(client, auth_headers, db):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-MALFORMED-001")
    assert created.status_code == 201, created.text

    body = "\n".join([
        "",                     # blank line
        "ONLY-ONE-FIELD",       # no tab — skipped
        f"GOOD-EMP-1\t{_device_local_ts(2)}\t0\t1",
    ])
    resp = client.post(
        "/iclock/cdata", params={"SN": "SN-MALFORMED-001", "table": "ATTLOG"},
        content=body, headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "OK: 1"
    row = db.query(AttendanceDetail).filter(AttendanceDetail.employee_id == "GOOD-EMP-1").one()
    assert len(json.loads(row.raw_events)) == 1


def test_non_attlog_table_acknowledged_without_ingesting(client, auth_headers, db):
    created = _add_adms_classic_device(client, auth_headers, serial="SN-OPERLOG-001")
    assert created.status_code == 201, created.text

    resp = client.post(
        "/iclock/cdata", params={"SN": "SN-OPERLOG-001", "table": "OPERLOG"},
        content="some enrollment data we don't care about",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.text.strip() == "OK"
