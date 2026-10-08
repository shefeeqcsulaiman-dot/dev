#!/usr/bin/env python3
"""
ALL-IN-ONE Daily Attendance Report
-----------------------------------
Connects to the ZKTeco/X Face Pro device and produces a formatted daily
attendance report for EVERY enrolled employee (not just those who clocked in),
AND forwards each punch to TaxFlow so the same data shows up in HRMS
(Today's Attendance, Present Today, payslips) without a separate live-sync
tool running.

Output columns:
Emp No. | AC-No. | Day | Name | Date | Clock In 1 | Clock Out 1 | Work Time 1 |
Clock In 2 | Clock Out 2 | Work Time 2 | ... (up to 5 sessions) |
Total in time | OT | Under Time | Absent | SICK | Holiday

Run this daily (manually, or scheduled via Windows Task Scheduler) — it is
NOT meant to run continuously like zk_bridge.py; it is a once-a-day batch
job that also happens to produce a local CSV/Excel report each time.

Usage
-----
1. Install dependencies:
       pip install pyzk pandas requests

2. Configure the settings below — set DEVICE_IP to your device's real LAN
   IP, and ETAXFLOW_DEVICE_KEY to a key generated from:
       HRMS -> Settings -> Biometric Devices -> Add Device -> copy the key
   (If downloaded from the Setup Guide right after creating a device, these
   are already filled in for you.)

3. Run:
       python daily_attendance_report.py

   Windows Task Scheduler: point a daily trigger at this script with your
   Python interpreter, e.g. Action = "C:\\Python312\\python.exe", Arguments =
   "C:\\path\\to\\daily_attendance_report.py".
"""

from zk import ZK
import pandas as pd
from datetime import datetime, timedelta
import os
import requests

# ================= CONFIG =================
DEVICE_IP = "192.168.1.201"      # device's own IP (Menu -> Comm. -> Ethernet)
DEVICE_PORT = 4370
COMM_PASSWORD = 0

OUTPUT_DIR = r"C:\attendance_exports"
RAW_MASTER_CSV = os.path.join(OUTPUT_DIR, "attendance_master.csv")   # raw punch log, kept for audit
USERS_CSV = os.path.join(OUTPUT_DIR, "users.csv")                     # full staff roster snapshot
REPORT_CSV = os.path.join(OUTPUT_DIR, "formatted_attendance_report.csv")
LOG_FILE = os.path.join(OUTPUT_DIR, "daily_export_log.txt")

DAYS_BACK = 2          # how many days of punches to pull each run (small overlap buffer)
STANDARD_HOURS = 8.0    # standard work day length, used for OT / Under Time
MAX_SESSIONS = 5        # max clock in/out pairs per day shown as columns (handles split shifts)
MIN_GAP_MINUTES = 5      # ignore duplicate/accidental double-taps within this many minutes

# --- TaxFlow ADMS push settings ---
PUSH_TO_ETAXFLOW = False                       # set True once ETAXFLOW_DEVICE_KEY is filled in
ETAXFLOW_URL = "https://e4cs.com/api/v1/adms"
ETAXFLOW_DEVICE_KEY = "YOUR_DEVICE_KEY"        # <-- paste your real device key here
PUSHED_LOG_CSV = os.path.join(r"C:\attendance_exports", "pushed_to_etaxflow.csv")
# ============================================


def log(message):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def format_hm(td):
    """Format a timedelta as HH:MM"""
    if pd.isna(td):
        return ""
    total_minutes = int(td.total_seconds() // 60)
    h, m = divmod(total_minutes, 60)
    return f"{h:02d}:{m:02d}"


def _punch_direction(punch_code):
    """
    Maps a ZKTeco raw punch-state code to a simple in/out bucket: 0/4 =
    check-in, 3 = break-in (also counted as "in" -- resuming work), 1/2/5 =
    check-out/break-out/OT-out. Unknown/missing/NaN codes default to "in",
    matching TaxFlow's own PunchIn.direction default. Shared by
    dedupe_punches()/pair_punches() and push_to_etaxflow() so both paths
    always agree on what a given punch means.
    """
    try:
        code = int(float(punch_code))
    except (TypeError, ValueError):
        return "in"
    return "out" if code in (1, 2, 5) else "in"


def dedupe_punches(events):
    """
    Drop accidental double-taps: if two punches for the same person happen
    within MIN_GAP_MINUTES of each other AND are the same direction, keep
    only the first and discard the rest until the gap grows large enough
    again. `events` is a sorted list of (timestamp, direction) tuples.

    Direction-aware on purpose: collapsing ANY two punches within the gap
    regardless of direction would treat a genuine short break (out, then
    back in 3 minutes later) as an accidental double-tap and silently drop
    one of the two real punches -- shifting every pairing for the rest of
    that day.
    """
    if not events:
        return events
    events = sorted(events, key=lambda e: e[0])
    filtered = [events[0]]
    for ts, direction in events[1:]:
        last_ts, last_direction = filtered[-1]
        if direction == last_direction and (ts - last_ts) < timedelta(minutes=MIN_GAP_MINUTES):
            continue
        filtered.append((ts, direction))
    return filtered


def pair_punches(events):
    """
    Pair a sorted list of (timestamp, direction) punch events into
    (clock_in, clock_out) sessions using each punch's REAL direction, not
    just its position in the list. Handles employees with multiple real
    sessions per day (split shifts, breaks):
      - a genuine "in" followed by an "out" closes a normal session;
      - two "in"s in a row (a missed punch-out) closes the previous
        session with Clock Out left blank, then opens a new one;
      - a stray "out" with no open session (e.g. a duplicate/misfired
        scan) has nothing to pair with and is dropped.
    """
    pairs = []
    open_in = None
    for ts, direction in events:
        if direction == "in":
            if open_in is not None:
                pairs.append((open_in, None))
            open_in = ts
        else:
            if open_in is not None:
                pairs.append((open_in, ts))
                open_in = None
            # else: stray "out" with no matching "in" -- nothing to pair, drop it.
    if open_in is not None:
        pairs.append((open_in, None))
    return pairs


def fetch_from_device():
    """Connect to the device and pull both the full user roster and attendance punches."""
    zk = ZK(DEVICE_IP, port=DEVICE_PORT, timeout=10, password=COMM_PASSWORD,
             force_udp=False, ommit_ping=True)
    conn = None

    try:
        conn = zk.connect()
        log(f"Connected to device: {conn.get_device_name()}")

        # --- Full staff roster (everyone enrolled, regardless of punches) ---
        users = conn.get_users()
        user_rows = [{
            "user_id": u.user_id,
            "name": u.name,
            "privilege": u.privilege,
            "card": u.card
        } for u in users]
        roster_df = pd.DataFrame(user_rows).sort_values("user_id")
        roster_df.to_csv(USERS_CSV, index=False)
        log(f"Saved {len(roster_df)} staff to {USERS_CSV}")

        # --- Attendance punches ---
        user_map = {u.user_id: u.name for u in users}
        conn.disable_device()
        try:
            attendance = conn.get_attendance()
        finally:
            # If get_attendance() throws (network blip, timeout, anything),
            # enable_device() must still run -- otherwise the device stays
            # disabled (can't accept live scans) until the next successful
            # run, which might not be until tomorrow.
            conn.enable_device()

        cutoff = datetime.now() - timedelta(days=DAYS_BACK)
        rows = []
        for a in attendance:
            if a.timestamp >= cutoff:
                rows.append({
                    "user_id": a.user_id,
                    "name": user_map.get(a.user_id, ""),
                    "timestamp": a.timestamp,
                    "status": getattr(a, "status", None),
                    "punch": getattr(a, "punch", None)
                })
        new_punches_df = pd.DataFrame(rows)
        log(f"Pulled {len(new_punches_df)} recent punches from device")

        return roster_df, new_punches_df

    finally:
        if conn:
            conn.disconnect()
            log("Disconnected.")


def update_master_csv(new_punches_df):
    """Append new punches to the running master CSV, de-duplicated."""
    if new_punches_df.empty:
        log("No new punches to add to master CSV.")
    else:
        if os.path.exists(RAW_MASTER_CSV):
            master_df = pd.read_csv(RAW_MASTER_CSV, parse_dates=["timestamp"])
            combined = pd.concat([master_df, new_punches_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["user_id", "timestamp"])
        else:
            combined = new_punches_df

        combined = combined.sort_values("timestamp")
        combined.to_csv(RAW_MASTER_CSV, index=False)
        log(f"Master punch log now has {len(combined)} total records.")

    if os.path.exists(RAW_MASTER_CSV):
        return pd.read_csv(RAW_MASTER_CSV, parse_dates=["timestamp"])
    return pd.DataFrame(columns=["user_id", "name", "timestamp", "status", "punch"])


def build_formatted_report(punches_df, roster_df):
    """Build the full report: every staff member x every date in the window, Absent marked where no punches."""
    if punches_df.empty:
        punches_df = pd.DataFrame(columns=["user_id", "name", "timestamp"])
    else:
        punches_df = punches_df.copy()

    # Normalize user_id to the same type on both sides -- roster comes straight
    # from the device, punches come back from a saved CSV, and those two can
    # end up as different dtypes (e.g. int vs str), which silently breaks matching.
    roster_df = roster_df.copy()
    roster_df["user_id"] = roster_df["user_id"].astype(str)
    if not punches_df.empty:
        punches_df["user_id"] = punches_df["user_id"].astype(str)

    if not punches_df.empty:
        punches_df = punches_df.sort_values(["user_id", "timestamp"])

    # Cover every date in the report window, AND any date that actually has
    # punches (in case device time is slightly off from the PC's clock) --
    # union of both so real punches never get silently dropped.
    today = datetime.now().date()
    window_dates = set(today - timedelta(days=i) for i in range(DAYS_BACK, -1, -1))

    # Pairing is done per EMPLOYEE across their full punch history, not per
    # calendar date -- grouping by date before pairing used to split an
    # overnight shift (clock in 23:50, clock out 00:10 the next day) into two
    # unrelated buckets: the clock-in was left permanently "open" (no Clock
    # Out shown) on the first day, and the clock-out became a stray "out"
    # with nothing to pair against on the next day, silently dropped by
    # pair_punches(). Pairing across the whole timeline first, then
    # attributing each resulting session to the calendar date its Clock In
    # falls on, keeps overnight shifts intact end to end.
    raw_dates_by_user = {}    # (user_id, date) -> True, for Absent detection only
    pairs_by_user_date = {}   # (user_id, date) -> list of (clock_in, clock_out_or_None)

    if not punches_df.empty:
        # Each event is a (timestamp, direction) tuple -- direction is what
        # dedupe_punches()/pair_punches() need to pair sessions correctly
        # instead of guessing by position.
        has_punch_col = "punch" in punches_df.columns
        for user_id, group in punches_df.groupby("user_id"):
            events = [
                (ts, _punch_direction(code if has_punch_col else None))
                for ts, code in zip(
                    group["timestamp"],
                    group["punch"] if has_punch_col else [None] * len(group),
                )
            ]
            for ts, _direction in events:
                raw_dates_by_user[(user_id, ts.date())] = True
            events = dedupe_punches(events)
            for cin, cout in pair_punches(events):
                pairs_by_user_date.setdefault((user_id, cin.date()), []).append((cin, cout))

    punch_dates = set(d for (_, d) in raw_dates_by_user.keys())

    all_dates = sorted(window_dates | punch_dates)
    log(f"Report will cover dates: {[d.strftime('%d/%m/%Y') for d in all_dates]}")
    log(f"Punch records available for {len(raw_dates_by_user)} (employee, date) combinations")

    report_rows = []

    for date in all_dates:
        for _, emp in roster_df.iterrows():
            user_id = emp["user_id"]
            name = emp["name"]
            pairs = pairs_by_user_date.get((user_id, date), [])
            has_activity = raw_dates_by_user.get((user_id, date), False)

            row = {
                "Emp No.": user_id,
                "AC-No.": user_id,
                "Day": date.strftime("%a").upper(),
                "Name": name,
                "Date": date.strftime("%d/%m/%Y"),
            }

            # Total work is summed from EVERY session that day, not just the
            # first MAX_SESSIONS shown in the printed columns below --
            # previously `pairs` was sliced to MAX_SESSIONS before this total
            # was computed, so a 6th+ punch-pair's hours vanished from Total
            # in time/OT/Under Time entirely instead of just being left off
            # the display.
            total_work = timedelta()
            for cin, cout in pairs:
                if cout:
                    total_work += cout - cin

            shown_pairs = pairs[:MAX_SESSIONS]
            for idx in range(MAX_SESSIONS):
                n = idx + 1
                if idx < len(shown_pairs):
                    cin, cout = shown_pairs[idx]
                    row[f"Clock In {n}"] = cin.strftime("%H:%M")
                    if cout:
                        # "(+1)" flags a clock-out that landed on the next
                        # calendar day (overnight shift) so the time isn't
                        # misread as earlier than the clock-in.
                        suffix = " (+1)" if cout.date() != cin.date() else ""
                        row[f"Clock Out {n}"] = cout.strftime("%H:%M") + suffix
                        row[f"Work Time {n}"] = format_hm(cout - cin)
                    else:
                        row[f"Clock Out {n}"] = ""
                        row[f"Work Time {n}"] = ""
                else:
                    row[f"Clock In {n}"] = ""
                    row[f"Clock Out {n}"] = ""
                    row[f"Work Time {n}"] = ""

            row["Total in time"] = format_hm(total_work)

            total_hours = total_work.total_seconds() / 3600
            ot_hours = max(0, total_hours - STANDARD_HOURS)
            under_hours = max(0, STANDARD_HOURS - total_hours)

            row["OT"] = format_hm(timedelta(hours=ot_hours))
            row["Under Time"] = format_hm(timedelta(hours=under_hours))
            row["Absent"] = "Yes" if not has_activity else ""
            row["SICK"] = ""   # manual / from HRMS leave records
            row["Holiday"] = ""  # manual / from HRMS holiday calendar

            report_rows.append(row)

    report_df = pd.DataFrame(report_rows)

    column_order = ["Emp No.", "AC-No.", "Day", "Name", "Date"]
    for i in range(1, MAX_SESSIONS + 1):
        column_order += [f"Clock In {i}", f"Clock Out {i}", f"Work Time {i}"]
    column_order += ["Total in time", "OT", "Under Time", "Absent", "SICK", "Holiday"]

    report_df = report_df[column_order]
    report_df = report_df.sort_values(["Date", "Emp No."])

    report_df.to_csv(REPORT_CSV, index=False)
    log(f"Saved formatted report with {len(report_df)} rows to {REPORT_CSV}")
    return report_df


def push_to_etaxflow(new_punches_df):
    """
    Push newly-fetched raw punches to TaxFlow's ADMS endpoint via HTTPS.
    TaxFlow's endpoint validates against a "PunchIn" schema -- it expects ONE
    individual scan event per request (employee_id/employee_name/punch_time/
    direction), not the aggregated daily report format and not a field called
    "timestamp" (punch_time is the only recognized name -- anything else is
    silently ignored, defaulting punch_time to "now" and direction to "in").
    The formatted report (Emp No., Clock In 1, etc.) is for your local
    CSV/Excel only and is NOT what gets pushed here.

    Direction is derived via the shared _punch_direction() helper (see its
    docstring for the exact code mapping) -- the same function
    dedupe_punches()/pair_punches() use to build the local report, so what
    gets pushed here always agrees with what the report shows.
    """
    if not PUSH_TO_ETAXFLOW:
        return
    if new_punches_df is None or new_punches_df.empty:
        log("No new punches to push to TaxFlow.")
        return
    if not ETAXFLOW_DEVICE_KEY or ETAXFLOW_DEVICE_KEY == "YOUR_DEVICE_KEY":
        log("TaxFlow push skipped: ETAXFLOW_DEVICE_KEY is empty or still the placeholder value.")
        return

    # Load record of what's already been pushed, to avoid duplicates
    already_pushed = set()
    if os.path.exists(PUSHED_LOG_CSV):
        try:
            pushed_df = pd.read_csv(PUSHED_LOG_CSV, dtype=str)
            if "employee_id" in pushed_df.columns and "punch_time" in pushed_df.columns:
                already_pushed = set(zip(pushed_df["employee_id"], pushed_df["punch_time"]))
            else:
                log("pushed_to_etaxflow.csv has an old/unexpected format -- starting fresh dedup list.")
        except Exception as e:
            log(f"Could not read {PUSHED_LOG_CSV}, starting fresh dedup list: {e}")

    headers = {
        "X-Device-Key": ETAXFLOW_DEVICE_KEY,
        "Content-Type": "application/json"
    }

    newly_pushed_rows = []
    success_count = 0
    fail_count = 0

    for _, punch in new_punches_df.iterrows():
        emp_id = str(punch["user_id"])
        emp_name = punch.get("name") or None
        punch_time = punch["timestamp"].isoformat()
        key = (emp_id, punch_time)
        if key in already_pushed:
            continue

        direction = _punch_direction(punch.get("punch"))

        payload = {
            "employee_id": emp_id,
            "employee_name": emp_name,
            "punch_time": punch_time,
            "direction": direction,
        }

        try:
            resp = requests.post(ETAXFLOW_URL, json=payload, headers=headers, timeout=10)
            if resp.status_code in (200, 201, 202):
                success_count += 1
                newly_pushed_rows.append({"employee_id": emp_id, "punch_time": punch_time})
            else:
                fail_count += 1
                log(f"TaxFlow push failed for employee {emp_id} at {punch_time}: "
                    f"HTTP {resp.status_code} - {resp.text[:300]}")
        except Exception as e:
            fail_count += 1
            log(f"TaxFlow push error for employee {emp_id} at {punch_time}: {e}")

    log(f"TaxFlow push: {success_count} succeeded, {fail_count} failed.")

    if newly_pushed_rows:
        newly_pushed_df = pd.DataFrame(newly_pushed_rows)
        if os.path.exists(PUSHED_LOG_CSV):
            try:
                existing = pd.read_csv(PUSHED_LOG_CSV, dtype=str)
                if "employee_id" in existing.columns and "punch_time" in existing.columns:
                    combined = pd.concat([existing, newly_pushed_df], ignore_index=True)
                else:
                    combined = newly_pushed_df
            except Exception:
                combined = newly_pushed_df
        else:
            combined = newly_pushed_df
        combined.to_csv(PUSHED_LOG_CSV, index=False)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    log("=== Starting daily attendance run ===")

    try:
        roster_df, new_punches_df = fetch_from_device()
        all_punches_df = update_master_csv(new_punches_df)
        report_df = build_formatted_report(all_punches_df, roster_df)
        push_to_etaxflow(new_punches_df)
        log("=== Run completed successfully ===")

    except Exception as e:
        log(f"ERROR: {e}")


if __name__ == "__main__":
    main()
