"""POST /attendance/devices/{id}/test for TCP/IP-mode devices.

TCP/IP devices (ZKTeco/Anviz, connected via zk_bridge.py) live on a private
LAN by design — that's the whole reason the bridge script has to run
locally. This endpoint runs on TaxFlow's own servers, which can never reach
a private-range IP directly, so testing it must fall back to a recent-punch
heuristic instead of a doomed socket connect. See routers/attendance.py's
test_device() TCP/IP branch.
"""
from datetime import UTC, datetime, timedelta

from app.models import AttendancePunch, BiometricDevice


def _add_tcp_device(client, headers, ip="192.168.1.201", name="Main Entrance"):
    resp = client.post("/api/v1/attendance/devices", json={
        "name": name,
        "device_type": "ZKTeco",
        "ip_address": ip,
        "port": 4370,
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_private_ip_with_no_punches_reports_not_synced_without_socket_attempt(client, auth_headers):
    device_id = _add_tcp_device(client, auth_headers)
    resp = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    # Previously this attempted a real socket.connect() to a private IP from
    # the test process (which could hang/fail unpredictably depending on the
    # sandbox's network policy) and, in production, would virtually always
    # time out even for a correctly-configured device.
    assert body["ok"] is False
    assert "zk_bridge.py" in body["message"]
    assert "private" in body["message"].lower()


def test_private_ip_with_recent_punches_reports_synced(client, auth_headers, db):
    device_id = _add_tcp_device(client, auth_headers)
    device = db.query(BiometricDevice).filter(BiometricDevice.id == device_id).first()
    db.add(AttendancePunch(
        company_id=device.company_id,
        device_id=device.id,
        employee_id="EMP001",
        punch_time=datetime.now(UTC) - timedelta(hours=1),
        punch_date=(datetime.now(UTC) - timedelta(hours=1)).date().isoformat(),
        direction="in",
        source="device",
    ))
    db.commit()

    resp = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "1 punches" in body["message"] or "synced" in body["message"].lower()


def test_public_ip_still_attempts_real_socket_check(client, auth_headers):
    # A genuinely public IP (rare, but not impossible) should still go
    # through the real reachability check rather than the punch-count
    # fallback — 203.0.113.0/24 is TEST-NET-3, reserved for documentation,
    # guaranteed unreachable, so this exercises the "Timeout"/refused path
    # without depending on any real external host.
    device_id = _add_tcp_device(client, auth_headers, ip="203.0.113.5")
    resp = client.post(f"/api/v1/attendance/devices/{device_id}/test", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "203.0.113.5" in body["message"]
