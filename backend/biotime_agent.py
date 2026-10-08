#!/usr/bin/env python3
"""
BioTime Agent — ETaxFlow HRMS
==============================
Pulls attendance transactions from a ZKTeco BioTime 9.5 server that is only
reachable on the CUSTOMER'S OWN network (not from the internet), and forwards
them to the ETaxFlow attendance API — same role as zk_bridge.py, but for a
BioTime server instead of a raw ZKTeco TCP/IP device.

Why this exists (docs/biometric-architecture.md §3): TaxFlow's built-in
BioTime integration (HRMS > Attendance > Devices > "ZKTeco BioTime Server")
works by TaxFlow's own servers calling OUT to the customer's BioTime server —
that requires the customer to expose BioTime to the internet, which many
IT/security policies won't allow. This script inverts the direction: it runs
ON the customer's network, where BioTime IS reachable, and pushes punches OUT
to ETaxFlow instead — the same "always initiate outbound" shape zk_bridge.py
already uses for direct-device customers.

This is NOT a replacement for the built-in BioTime Server connection — if a
customer's BioTime server genuinely is reachable from the internet, that
simpler pull integration (no script to run) is still the right choice. This
script is specifically for the case where it isn't.

Usage
-----
1. Install dependencies:
       pip install requests
   (no pyzk needed — this talks to BioTime's own REST API, not the raw
   ZKTeco TCP/IP protocol zk_bridge.py uses)

2. Configure the settings below (or export them as environment variables,
   or use biotime_agent.conf — see below).

3. Generate a device API key from ETaxFlow:
       HRMS → Settings → Biometric Devices → Add Device
       (pick any device type other than "ZKTeco BioTime Server" — that type
       is reserved for the built-in pull connection and issues no API key;
       "BioTime via Agent" is a reasonable label to use)

4. Run:
       python biotime_agent.py

   To keep it running automatically after a reboot without a terminal
   window open (Windows only):
       python biotime_agent.py --install-startup

   For systemd / PM2 (Linux):
       pm2 start biotime_agent.py --interpreter python3 --name biotime-agent

Environment variables (override defaults)
-----------------------------------------
  BIOTIME_BASE_URL       Customer's local BioTime server, e.g. http://192.168.1.50:8098
  BIOTIME_USERNAME       BioTime login username
  BIOTIME_PASSWORD       BioTime login password
  POLL_INTERVAL          Seconds between sync cycles (default 60)
  API_BASE_URL           ETaxFlow API base (e.g. https://e4cs.com)
  DEVICE_API_KEY         API key from HRMS → Biometric Devices
  DEVICE_UTC_OFFSET_HOURS  Override if the BioTime server's clock isn't UAE
                            local time (default 4 — UAE has no DST)
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
# Values are read from biotime_agent.conf (same folder as this script) first,
# then from environment variables, then fall back to the defaults below.
# Using a config file keeps credentials out of shell history and process
# lists — same convention as zk_bridge.conf.
#
# biotime_agent.conf example:
#   DEVICE_API_KEY=your_key_here
#   BIOTIME_BASE_URL=http://192.168.1.50:8098
#   BIOTIME_USERNAME=admin
#   BIOTIME_PASSWORD=changeme
#   API_BASE_URL=https://e4cs.com

def _load_conf() -> dict[str, str]:
    conf: dict[str, str] = {}
    conf_path = pathlib.Path(__file__).parent / "biotime_agent.conf"
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

BIOTIME_BASE_URL = _get("BIOTIME_BASE_URL", "").rstrip("/")
BIOTIME_USERNAME = _get("BIOTIME_USERNAME", "")
BIOTIME_PASSWORD = _get("BIOTIME_PASSWORD", "")
POLL_INTERVAL    = int(_get("POLL_INTERVAL", "60"))
API_BASE_URL     = _get("API_BASE_URL", "https://e4cs.com").rstrip("/")
DEVICE_API_KEY   = _get("DEVICE_API_KEY", "")
# BioTime reports attendance in the server's own local clock (naive
# datetime, no tzinfo) — same situation zk_bridge.py handles for raw ZKTeco
# devices. UAE has no DST, so this is a fixed UTC+4 offset by default;
# override via DEVICE_UTC_OFFSET_HOURS if the BioTime server's clock is set
# to a different timezone.
DEVICE_UTC_OFFSET_HOURS = float(_get("DEVICE_UTC_OFFSET_HOURS", "4"))

PUNCH_ENDPOINT = f"{API_BASE_URL}/api/v1/punch"
_REQUEST_TIMEOUT = 15.0
_FIRST_SYNC_LOOKBACK_HOURS = 24

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("biotime_agent")

# ── State ─────────────────────────────────────────────────────────────────────
# Persisted to disk so a restart (crash, redeploy, pm2 restart) doesn't forget
# where syncing left off — same convention as zk_bridge_state.json. Only
# advances past a punch once it has been successfully posted to ETaxFlow;
# a post failure stops the cycle without advancing, so nothing is ever
# silently skipped — the whole remaining window is retried next cycle. The
# ETaxFlow /punch endpoint's own idempotency guard (company + employee +
# punch_time + device) makes re-sending an already-received punch harmless.

_STATE_PATH = pathlib.Path(__file__).parent / "biotime_agent_state.json"


def _load_last_sync() -> datetime | None:
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        return datetime.fromisoformat(raw["last_sync"])
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None


def _save_last_sync(value: datetime) -> None:
    try:
        _STATE_PATH.write_text(json.dumps({"last_sync": value.isoformat()}), encoding="utf-8")
    except OSError as exc:
        log.warning("Could not persist sync state to %s: %s", _STATE_PATH, exc)


# ── BioTime REST client ───────────────────────────────────────────────────────
# Same two calls as backend/app/biotime_client.py (get_token, list_transactions)
# — deliberately duplicated here rather than imported, since this script runs
# standalone on a customer machine that doesn't have the ETaxFlow backend
# installed. Keep these two functions' request/response handling in sync with
# biotime_client.py if either changes.

class BioTimeError(Exception):
    pass


def _get_token() -> str:
    url = f"{BIOTIME_BASE_URL}/jwt-api-token-auth/"
    try:
        resp = requests.post(
            url,
            json={"username": BIOTIME_USERNAME, "password": BIOTIME_PASSWORD},
            headers={"Content-Type": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise BioTimeError(f"Could not reach BioTime server at {BIOTIME_BASE_URL}: {exc}") from exc
    if resp.status_code != 200:
        raise BioTimeError(f"BioTime login failed ({resp.status_code}): {resp.text[:300]}")
    token = resp.json().get("token")
    if not token:
        raise BioTimeError("BioTime login succeeded but returned no token")
    return token


def _list_transactions(token: str, start_time: datetime, end_time: datetime) -> list[dict]:
    url: str | None = f"{BIOTIME_BASE_URL}/iclock/api/transactions/"
    headers = {"Authorization": f"JWT {token}", "Content-Type": "application/json"}
    params: dict[str, str] | None = {
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "page_size": "200",
    }
    transactions: list[dict] = []
    while url:
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=_REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            raise BioTimeError(f"Could not reach BioTime server: {exc}") from exc
        if resp.status_code != 200:
            raise BioTimeError(f"BioTime transaction pull failed ({resp.status_code}): {resp.text[:300]}")
        body = resp.json()
        transactions.extend(body.get("data") or [])
        url = body.get("next") or None
        params = None  # "next" already carries the query string
    return transactions


def _parse_punch_time(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            naive = datetime.strptime(raw, fmt)
            return (naive - timedelta(hours=DEVICE_UTC_OFFSET_HOURS)).replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def _map_direction(row: dict) -> str:
    """BioTime's punch_state convention varies by firmware/config — same
    fallback-to-"unknown" logic as backend/app/biotime_sync.py's
    _map_direction(), so a value neither script recognizes yet gets flagged
    for review rather than silently mislabeled as in/out."""
    state = str(row.get("punch_state", row.get("punch_state_display", ""))).strip().lower()
    if state in ("0", "check in", "checkin", "in"):
        return "in"
    if state in ("1", "check out", "checkout", "out"):
        return "out"
    return "unknown"


# ── Posting to ETaxFlow ────────────────────────────────────────────────────────

def _post_punch(employee_id: str, employee_name: str | None, punch_time: datetime, direction: str) -> bool:
    """POST a single punch to the ETaxFlow API. Returns True on success.
    Same endpoint and header zk_bridge.py uses (POST /api/v1/punch,
    X-Device-Key) — an agent-pushed punch and a zk_bridge-pushed punch are
    indistinguishable to the backend, both land with source="device"."""
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
        log.warning("API returned %s for employee %s @ %s: %s", resp.status_code, employee_id, punch_time, resp.text[:200])
        return False
    except requests.RequestException as exc:
        log.error("Failed to POST punch: %s", exc)
        return False


# ── Main sync loop ────────────────────────────────────────────────────────────

def _sync_once() -> None:
    now = datetime.now(timezone.utc)
    last_sync = _load_last_sync()
    start = last_sync or (now - timedelta(hours=_FIRST_SYNC_LOOKBACK_HOURS))

    token = _get_token()
    rows = _list_transactions(token, start, now)

    # Process oldest-first so the watermark only ever advances through a
    # contiguous run of successes — see the _STATE_PATH comment above.
    parsed = []
    for row in rows:
        emp_code = str(row.get("emp_code") or row.get("employee") or "").strip()
        raw_time = row.get("punch_time") or row.get("upload_time")
        if not emp_code or not raw_time:
            continue
        punch_time = _parse_punch_time(str(raw_time))
        if not punch_time:
            continue
        parsed.append((punch_time, emp_code, row))
    parsed.sort(key=lambda item: item[0])

    synced = 0
    watermark = last_sync
    for punch_time, emp_code, row in parsed:
        if last_sync and punch_time <= last_sync:
            continue  # already synced in a previous cycle
        emp_name = row.get("emp_name") or row.get("first_name")
        direction = _map_direction(row)
        ok = _post_punch(emp_code, emp_name, punch_time, direction)
        if not ok:
            log.warning("Stopping this cycle at the first failed punch — will retry from here next cycle")
            break
        synced += 1
        watermark = punch_time

    if watermark and watermark != last_sync:
        _save_last_sync(watermark)

    log.info("Synced %d new punch(es) (%d found in window)", synced, len(parsed))


# ── Windows startup registration ─────────────────────────────────────────────
# Same per-user (no Administrator needed) Scheduled Task approach as
# zk_bridge.py — deliberately duplicated rather than shared via an import;
# both scripts are meant to be downloaded and run standalone as a single
# file. Keep in sync with zk_bridge.py's _install_startup_task() if either
# changes.

def _install_startup_task(display_name: str) -> None:
    if sys.platform != "win32":
        sys.exit("--install-startup is only supported on Windows (uses schtasks).")
    import subprocess
    task_name = f"TaxFlow{display_name}"
    if getattr(sys, "frozen", False):
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


def main() -> None:
    if "--install-startup" in sys.argv:
        _install_startup_task("BioTimeAgent")
        return

    missing = [
        name for name, value in (
            ("BIOTIME_BASE_URL", BIOTIME_BASE_URL),
            ("BIOTIME_USERNAME", BIOTIME_USERNAME),
            ("BIOTIME_PASSWORD", BIOTIME_PASSWORD),
            ("DEVICE_API_KEY", DEVICE_API_KEY),
        ) if not value
    ]
    if missing:
        sys.exit(f"Error: missing required setting(s): {', '.join(missing)} — see biotime_agent.conf or the env vars listed at the top of this file.")

    log.info("BioTime Agent starting — BioTime: %s  API: %s", BIOTIME_BASE_URL, API_BASE_URL)

    while True:
        try:
            _sync_once()
        except BioTimeError as exc:
            log.error("BioTime error: %s", exc)
        except Exception as exc:  # keep the loop alive across any unexpected failure
            log.exception("Unexpected error during sync: %s", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
