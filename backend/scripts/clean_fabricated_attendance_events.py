"""One-off cleanup for attendance_details rows polluted with fabricated
`datetime.now()` events.

Background: before /adms and /punch learned the flattened-row payload shape
(work_date + clock_in_N / raw_events), a remote report script pushing that
shape had every field silently dropped; punch_time defaulted to
datetime.now(UTC), producing one fake "in" event per push. Repeated runs
accumulated dozens of them per employee/day, and _pair_day_punches then
derived clock_in_1/2/3 and session_count from that garbage.

`created_at` was never involved -- clock_in_1 is always raw_events[0]'s
punch_time. The tell-tale of a fabricated event is a SUB-SECOND punch_time
("...:30.119305+00:00"): a real ZKTeco/ADMS scan always reports whole
seconds.

This script keeps only whole-second events per row, then:
  - if nothing genuine remains -> resets the row to a clean ABSENT marker
    (clock_in/out NULL, work_seconds NULL, total/ot 0, under_seconds =
    8h, session_count 0, raw_events []).
  - otherwise -> re-runs the real pairing engine (_recompute_day_fields)
    over just the genuine events.
Rows whose events are ALL genuine are left byte-for-byte untouched.

Dry-run by default. Pass --apply to write. Optionally --company <id>.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.attendance_store import _recompute_day_fields  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import AttendanceDetail  # noqa: E402

_SUBSECOND = re.compile(r"\.\d")  # fractional seconds anywhere in the timestamp
_STANDARD_HOURS = 8.0


def _is_genuine(event: dict) -> bool:
    pt = event.get("punch_time") or ""
    return not _SUBSECOND.search(pt)


def _reset_to_absent(row: AttendanceDetail) -> None:
    for n in range(1, 6):
        setattr(row, f"clock_in_{n}", None)
        setattr(row, f"clock_out_{n}", None)
        setattr(row, f"work_seconds_{n}", None)
    row.total_seconds = 0
    row.ot_seconds = 0
    row.under_seconds = int(_STANDARD_HOURS * 3600)
    row.session_count = 0
    row.raw_events = "[]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--company", help="restrict to one company_id")
    args = ap.parse_args()

    db = SessionLocal()
    q = db.query(AttendanceDetail)
    if args.company:
        q = q.filter(AttendanceDetail.company_id == args.company)

    reset_count = recomputed_count = untouched_count = 0
    for row in q.yield_per(500):
        try:
            events = json.loads(row.raw_events or "[]")
        except (ValueError, TypeError):
            events = []
        if not events:
            untouched_count += 1
            continue
        genuine = [e for e in events if _is_genuine(e)]
        if len(genuine) == len(events):
            untouched_count += 1
            continue

        fabricated = len(events) - len(genuine)
        if genuine:
            recomputed_count += 1
            action = f"recompute from {len(genuine)} genuine (drop {fabricated} fabricated)"
            if args.apply:
                _recompute_day_fields(row, genuine, _STANDARD_HOURS)
        else:
            reset_count += 1
            action = f"reset to ABSENT (all {fabricated} events fabricated)"
            if args.apply:
                _reset_to_absent(row)
        print(f"{row.company_id} {row.employee_id} {row.work_date}: {action}")

    if args.apply:
        db.commit()
        print(f"\nAPPLIED. reset={reset_count} recomputed={recomputed_count} untouched={untouched_count}")
    else:
        print(f"\nDRY RUN. would reset={reset_count} recompute={recomputed_count} untouched={untouched_count}")
        print("Re-run with --apply to write.")
    db.close()


if __name__ == "__main__":
    main()
