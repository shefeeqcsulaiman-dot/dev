#!/usr/bin/env python3
"""
ZKTeco Biometric Bridge — ETaxFlow HRMS
========================================
Connects to a ZKTeco (or compatible) attendance device over TCP/IP and
forwards every punch record to the ETaxFlow attendance API.

Supports:
  • ZKTeco (pyzk library) — most UAE devices (ZK4500, ZK9500, iClock series)
  • Suprema / Hikvision / Anviz — use their built-in HTTP push instead of this
    script (see "Webhook receiver helper" below); pyzk is required to run this
    script at all, there is no non-pyzk fallback

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

   To keep it running automatically after a reboot without a terminal
   window open (Windows only):
       python zk_bridge.py --install-startup

   For systemd / PM2 (Linux):
       pm2 start zk_bridge.py --interpreter python3 --name zk-bridge

Environment variables (override defaults)
-----------------------------------------
  ZK_DEVICE_IP        IP address of the biometric device
  ZK_DEVICE_PORT      TCP port (default 4370)
  ZK_POLL_INTERVAL    Seconds between polls (default 30)
  API_BASE_URL        ETaxFlow API base (e.g. https://dev.etaxflow.com)
  DEVICE_API_KEY      API key from HRMS → Biometric Devices
"""

import json
import os
import sys
import time
import logging
import pathlib
from datetime import datetime, timedelta, timezone

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
#   API_BASE_URL=https://dev.etaxflow.com

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

def _is_explicitly_set(key: str) -> bool:
    return key in _conf or key in os.environ

_ZK_DEVICE_IP_EXPLICIT = _is_explicitly_set("ZK_DEVICE_IP")
ZK_DEVICE_IP     = _get("ZK_DEVICE_IP",     "192.168.1.201")
ZK_DEVICE_PORT   = int(_get("ZK_DEVICE_PORT", "4370"))
ZK_POLL_INTERVAL = int(_get("ZK_POLL_INTERVAL", "30"))
API_BASE_URL     = _get("API_BASE_URL",     "https://dev.etaxflow.com").rstrip("/")
DEVICE_API_KEY   = _get("DEVICE_API_KEY",   "")
# ZKTeco devices report attendance in the device's own local clock (naive
# datetime, no tzinfo). UAE has no DST, so this is a fixed UTC+4 offset by
# default — override via DEVICE_UTC_OFFSET_HOURS if the device clock is set
# to a different timezone.
DEVICE_UTC_OFFSET_HOURS = float(_get("DEVICE_UTC_OFFSET_HOURS", "4"))

PUNCH_ENDPOINT = f"{API_BASE_URL}/api/v1/punch"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("zk_bridge")

# ── State ─────────────────────────────────────────────────────────────────────
# Persisted to disk so a restart (crash, redeploy, pm2 restart) doesn't forget
# the last synced punch and resend the device's entire attendance log.

_STATE_PATH = pathlib.Path(__file__).parent / "zk_bridge_state.json"


def _load_last_punch_time() -> datetime | None:
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(raw["last_punch_time"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _save_last_punch_time(value: datetime) -> None:
    try:
        _STATE_PATH.write_text(json.dumps({"last_punch_time": value.isoformat()}), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not persist sync state to %s: %s", _STATE_PATH, exc)


_last_punch_time: datetime | None = _load_last_punch_time()   # track last sent punch to avoid duplicates


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
        conn = None
        try:
            conn = zk.connect()
            conn.disable_device()
            log.info("Connected to device at %s:%s", ZK_DEVICE_IP, ZK_DEVICE_PORT)
            # Printed every cycle (not just at startup) so it's visible in
            # whatever window/log the operator is actually looking at when
            # diagnosing a wrong clock-in time -- this offset assumption is
            # the single most common source of a systematically-wrong
            # (not random) check-in time: if the device's own clock isn't
            # ACTUALLY set to this offset from UTC, every punch converts
            # wrong by a fixed number of hours.
            log.info("Device-to-UTC offset assumed: +%.1fh (DEVICE_UTC_OFFSET_HOURS) -- "
                     "if check-in times are off by a fixed number of hours, this is almost "
                     "always the culprit: verify the device's own clock/timezone setting "
                     "matches this value, not the server's.", DEVICE_UTC_OFFSET_HOURS)

            users = {u.user_id: u.name for u in conn.get_users()}
            log.info("Loaded %d users from device", len(users))

            attendances = conn.get_attendance()
            new_punches = 0
            for att in attendances:
                raw_device_time = att.timestamp  # device's own local wall-clock reading, unconverted
                punch_time = att.timestamp
                if isinstance(punch_time, datetime) and punch_time.tzinfo is None:
                    # Device clock is local time, not UTC — convert before mislabeling
                    # it, otherwise every punch looks DEVICE_UTC_OFFSET_HOURS in the
                    # future and the API rejects it as an invalid future timestamp.
                    punch_time = (punch_time - timedelta(hours=DEVICE_UTC_OFFSET_HOURS)).replace(tzinfo=timezone.utc)

                if _last_punch_time and punch_time <= _last_punch_time:
                    continue

                direction = "out" if getattr(att, "punch", 0) == 1 else "in"
                emp_name = users.get(att.user_id, "")
                ok = _post_punch(att.user_id, emp_name, punch_time, direction)
                # Logged for every punch actually sent (not just a summary
                # count) so "which data is getting sent" is directly visible
                # in the console -- compare raw_device_time against what your
                # own eyes/watch saw at the scanner to confirm the offset
                # above is correct for this device.
                log.info(
                    "Punch %s: user=%s (%s) direction=%s device_clock=%s -> sent_utc=%s",
                    "OK" if ok else "FAILED",
                    att.user_id, emp_name or "unknown name", direction,
                    raw_device_time, punch_time.isoformat(),
                )
                if ok:
                    new_punches += 1
                    if _last_punch_time is None or punch_time > _last_punch_time:
                        _last_punch_time = punch_time

            log.info("Synced %d new punches (total on device: %d)", new_punches, len(attendances))
            if new_punches and _last_punch_time is not None:
                _save_last_punch_time(_last_punch_time)

        except Exception as exc:
            log.error("ZK error: %s", exc)
        finally:
            # disable_device() above puts the physical scanner in a locked/
            # busy state until enable_device() runs — previously that call
            # (and disconnect()) only ran on the happy path, so any error in
            # between (e.g. get_attendance() failing on a dropped connection)
            # left the device unable to accept punches, and the socket open,
            # until the device's own internal timeout eventually recovered
            # it. Always attempt both, independently, once a connection was
            # actually established.
            if conn is not None:
                try:
                    conn.enable_device()
                except Exception as exc:
                    log.error("Failed to re-enable device after error: %s", exc)
                try:
                    conn.disconnect()
                except Exception as exc:
                    log.error("Failed to disconnect cleanly: %s", exc)

        time.sleep(ZK_POLL_INTERVAL)


# ── Webhook receiver helper ───────────────────────────────────────────────────
#
#  For Suprema, Hikvision, Anviz devices that support HTTP push:
#  Configure the device to POST to:
#    POST {API_BASE_URL}/api/v1/punch
#  with the following JSON body:
#    {"employee_id": "<badge_id>", "punch_time": "<ISO8601>", "direction": "in"}
#  and header:
#    X-Device-Key: <your_api_key>
#
#  The ETaxFlow backend already handles this endpoint — no additional
#  software needed for HTTP-push devices.


# ── Windows startup registration ─────────────────────────────────────────────
# Run `python zk_bridge.py --install-startup` (or the packaged .exe with the
# same flag) once, and this registers a per-user Windows Scheduled Task that
# starts the script automatically on login — no terminal window needs to stay
# open, and no Administrator elevation is required (a machine-wide/SYSTEM
# task would need that, undercutting the point of making this easier). This
# is deliberately duplicated in biotime_agent.py rather than shared via an
# import — both scripts are meant to be downloaded and run standalone as a
# single file, with no other project files alongside them on a customer PC.

def _install_startup_task(display_name: str) -> None:
    if sys.platform != "win32":
        sys.exit("--install-startup is only supported on Windows (uses schtasks).")
    import subprocess
    task_name = f"TaxFlow{display_name}"
    if getattr(sys, "frozen", False):
        # Packaged PyInstaller .exe — self-contained, no interpreter needed.
        command = f'"{sys.executable}"'
    else:
        command = f'"{sys.executable}" "{pathlib.Path(__file__).resolve()}"'
    result = subprocess.run(
        ["schtasks", "/create", "/sc", "onlogon", "/tn", task_name, "/tr", command, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"Failed to create scheduled task: {(result.stderr or result.stdout).strip()}")
    print(f"Installed as a Windows Scheduled Task ('{task_name}') — it will start automatically the next time you log in.")
    print(f'To run it immediately without logging out: schtasks /run /tn "{task_name}"')
    print(f'To remove it later: schtasks /delete /tn "{task_name}" /f')


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if "--install-startup" in sys.argv:
        _install_startup_task("ZkBridge")
        return

    if not DEVICE_API_KEY:
        sys.exit("Error: DEVICE_API_KEY is not set. Generate one in HRMS → Settings → Biometric Devices.")
    if not _ZK_DEVICE_IP_EXPLICIT:
        # Previously just logged a warning and kept running against the
        # placeholder IP — meaning a genuinely unconfigured install looped
        # forever failing silently instead of stopping with an actionable
        # message. A real device IP that happens to equal the placeholder
        # value is still accepted; only a config-file/env var that was never
        # actually set triggers this.
        sys.exit(
            "Error: no device IP configured. Add ZK_DEVICE_IP=<your device's IP> to "
            "zk_bridge.conf next to this script (see HRMS -> Attendance -> Biometric "
            "Devices -> Setup Guide for a ready-made download), or set it as an "
            "environment variable."
        )

    log.info("ZK Bridge starting — device: %s:%s  API: %s", ZK_DEVICE_IP, ZK_DEVICE_PORT, API_BASE_URL)

    try:
        import zk as _  # type: ignore[import]
    except ImportError:
        sys.exit("Missing dependency: pip install pyzk (required — this script has no working fallback without it)")

    log.info("pyzk found — using ZK protocol driver")
    _run_pyzk()


if __name__ == "__main__":
    main()
