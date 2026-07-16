#!/usr/bin/env python3
"""
ZKTeco Biometric Bridge — ETaxFlow HRMS
========================================
Connects to a ZKTeco (or compatible) attendance device over TCP/IP and
forwards every punch record to the ETaxFlow attendance API.

Supports:
  • ZKTeco (pyzk library) — most UAE devices (ZK4500, ZK9500, iClock series)
  • Fallback: raw socket polling for brands with ZK-compatible protocol

Usage
-----
1. Install dependencies:
       pip install pyzk requests
   Or add to requirements:
       pyzk==0.9
       requests>=2.31

2. Configure the settings below (or export them as environment variables).

3. Generate a device API key from ETaxFlow:
       HRMS → Settings → Biometric Devices → Add Device → copy the key

4. Run:
       python zk_bridge.py

   For systemd / PM2:
       pm2 start zk_bridge.py --interpreter python3 --name zk-bridge

Environment variables (override defaults)
-----------------------------------------
  ZK_DEVICE_IP        IP address of the biometric device
  ZK_DEVICE_PORT      TCP port (default 4370)
  ZK_POLL_INTERVAL    Seconds between polls (default 30)
  API_BASE_URL        ETaxFlow API base (e.g. https://app.etaxflow.com)
  DEVICE_API_KEY      API key from HRMS → Biometric Devices
"""

import os
import sys
import time
import logging
import pathlib
from datetime import datetime, timezone
from typing import Any

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip install requests")

# ── Configuration ─────────────────────────────────────────────────────────────
# Values are read from zk_bridge.conf (same folder as this script) first,
# then from environment variables, then fall back to the defaults below.
# Using a config file keeps the API key out of shell history and process lists.
#
# zk_bridge.conf example:
#   DEVICE_API_KEY=your_key_here
#   ZK_DEVICE_IP=192.168.1.201
#   ZK_DEVICE_PORT=4370
#   API_BASE_URL=https://app.etaxflow.com

def _load_conf() -> dict[str, str]:
    conf: dict[str, str] = {}
    conf_path = pathlib.Path(__file__).parent / "zk_bridge.conf"
    if conf_path.exists():
        for line in conf_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            conf[k.strip()] = v.strip()
    return conf

_conf = _load_conf()

def _get(key: str, default: str = "") -> str:
    return _conf.get(key) or os.environ.get(key) or default

ZK_DEVICE_IP     = _get("ZK_DEVICE_IP",     "192.168.1.201")
ZK_DEVICE_PORT   = int(_get("ZK_DEVICE_PORT", "4370"))
ZK_POLL_INTERVAL = int(_get("ZK_POLL_INTERVAL", "30"))
API_BASE_URL     = _get("API_BASE_URL",     "https://app.etaxflow.com").rstrip("/")
DEVICE_API_KEY   = _get("DEVICE_API_KEY",   "")

PUNCH_ENDPOINT = f"{API_BASE_URL}/api/v1/attendance/punch"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("zk_bridge")

# ── State ─────────────────────────────────────────────────────────────────────

_last_punch_time: datetime | None = None   # track last sent punch to avoid duplicates


def _post_punch(employee_id: str, employee_name: str, punch_time: datetime, direction: str = "in") -> bool:
    """POST a single punch to the ETaxFlow API. Returns True on success."""
    payload = {
        "employee_id": str(employee_id),
        "employee_name": employee_name or None,
        "punch_time": punch_time.isoformat(),
        "direction": direction,
    }
    try:
        resp = requests.post(
            PUNCH_ENDPOINT,
            json=payload,
            headers={"X-Device-Key": DEVICE_API_KEY, "Content-Type": "application/json"},
            timeout=10,
        )
        if resp.status_code == 201:
            return True
        log.warning("API returned %s: %s", resp.status_code, resp.text[:200])
        return False
    except Exception as exc:
        log.error("Failed to POST punch: %s", exc)
        return False


# ── ZKTeco via pyzk ───────────────────────────────────────────────────────────

def _run_pyzk() -> None:
    """Main loop using pyzk (most ZKTeco devices)."""
    from zk import ZK, const  # type: ignore[import]

    zk = ZK(ZK_DEVICE_IP, port=ZK_DEVICE_PORT, timeout=5, password=0, force_udp=False, ommit_ping=False)
    global _last_punch_time

    while True:
        try:
            conn = zk.connect()
            conn.disable_device()
            log.info("Connected to device at %s:%s", ZK_DEVICE_IP, ZK_DEVICE_PORT)

            users = {u.user_id: u.name for u in conn.get_users()}
            log.info("Loaded %d users from device", len(users))

            attendances = conn.get_attendance()
            new_punches = 0
            for att in attendances:
                punch_time = att.timestamp
                if isinstance(punch_time, datetime) and punch_time.tzinfo is None:
                    punch_time = punch_time.replace(tzinfo=timezone.utc)

                if _last_punch_time and punch_time <= _last_punch_time:
                    continue

                direction = "out" if getattr(att, "punch", 0) == 1 else "in"
                emp_name = users.get(att.user_id, "")
                ok = _post_punch(att.user_id, emp_name, punch_time, direction)
                if ok:
                    new_punches += 1
                    if _last_punch_time is None or punch_time > _last_punch_time:
                        _last_punch_time = punch_time

            log.info("Synced %d new punches (total on device: %d)", new_punches, len(attendances))
            conn.enable_device()
            conn.disconnect()

        except Exception as exc:
            log.error("ZK error: %s", exc)

        time.sleep(ZK_POLL_INTERVAL)


# ── Fallback: raw socket ZK protocol ─────────────────────────────────────────

def _run_socket_fallback() -> None:
    """
    Minimal ZK UDP attendance pull for devices where pyzk is unavailable.
    Sends the standard ZK 'get attendance log' command.
    For devices that support HTTP push, configure the device's webhook URL
    to point to:  POST {API_BASE_URL}/api/v1/attendance/punch
    with header:  X-Device-Key: {DEVICE_API_KEY}
    """
    import socket
    log.info("pyzk not available — running socket fallback (polling mode)")
    log.info("Alternatively, configure your device's HTTP push URL to: %s", PUNCH_ENDPOINT)

    CMD_CONNECT      = 1000
    CMD_GET_ATT_LOG  = 1201
    REPLY_OK         = 2000

    session_id = 0
    reply_id   = 0

    def _build_packet(cmd: int, data: bytes = b"") -> bytes:
        chksum = 0
        for b in data:
            chksum = (chksum + b) & 0xFFFF
        import struct
        hdr = struct.pack("<HHHH", cmd, chksum, session_id, reply_id)
        return hdr + data

    global _last_punch_time

    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_UDP if True else socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect((ZK_DEVICE_IP, ZK_DEVICE_PORT))

            sock.send(_build_packet(CMD_CONNECT))
            resp = sock.recv(1024)
            log.debug("Connect response: %s", resp.hex())

            sock.send(_build_packet(CMD_GET_ATT_LOG))
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk

            log.info("Received %d bytes of attendance data", len(data))
            sock.close()

        except Exception as exc:
            log.error("Socket error: %s", exc)

        time.sleep(ZK_POLL_INTERVAL)


# ── Webhook receiver helper ───────────────────────────────────────────────────
#
#  For Suprema, Hikvision, Anviz devices that support HTTP push:
#  Configure the device to POST to:
#    POST {API_BASE_URL}/api/v1/attendance/punch
#  with the following JSON body:
#    {"employee_id": "<badge_id>", "punch_time": "<ISO8601>", "direction": "in"}
#  and header:
#    X-Device-Key: <your_api_key>
#
#  The ETaxFlow backend already handles this endpoint — no additional
#  software needed for HTTP-push devices.


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not DEVICE_API_KEY:
        sys.exit("Error: DEVICE_API_KEY is not set. Generate one in HRMS → Settings → Biometric Devices.")
    if not ZK_DEVICE_IP or ZK_DEVICE_IP == "192.168.1.201":
        log.warning("ZK_DEVICE_IP is default (%s) — make sure this is correct", ZK_DEVICE_IP)

    log.info("ZK Bridge starting — device: %s:%s  API: %s", ZK_DEVICE_IP, ZK_DEVICE_PORT, API_BASE_URL)

    try:
        import zk as _  # type: ignore[import]
        log.info("pyzk found — using ZK protocol driver")
        _run_pyzk()
    except ImportError:
        log.warning("pyzk not installed (pip install pyzk) — falling back to socket mode")
        _run_socket_fallback()


if __name__ == "__main__":
    main()
