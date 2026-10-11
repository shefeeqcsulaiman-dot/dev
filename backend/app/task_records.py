"""Tasks as real rows (the task_records table), step A of moving them out of JSON.

Every save, edit and delete of a "tasks" app-data record updates its row in task_records
(app/record_mirror.py). The JSON record is still the source of truth; the rows can be
checked or rebuilt with `python -m app.task_records [--rebuild]`. Reads that only need
a few fields (is a task still open? whose is it? when is it due?) use this table instead
of decoding every task's JSON; the next steps (docs/scaling-plan-10k.md) move the rest.

Values are normalised on the way in: is_open follows app/workflow_status.py (the same
rule the bootstrap uses), progress is a whole number 0-100 (or empty when it isn't a
number), due_date is YYYY-MM-DD or empty.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, TaskRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror
from app.workflow_status import is_finished_status

COLLECTIONS = ("tasks",)
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPARED = ("record_key", "title", "department", "assigned_to", "assigned_to_name", "status", "is_open",
             "priority", "progress", "due_date", "repeat")


def _progress(value: Any) -> int | None:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))


def _day(value: Any) -> str | None:
    day = str(value or "").strip()[:10]
    return day if _ISO_DAY.match(day) else None


def task_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    status = _text(doc.get("status"), 40)
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        "title": _text(doc.get("title"), 255) or "",
        "department": _text(doc.get("department"), 120),
        "assigned_to": _text(doc.get("assigned_to"), 80),
        "assigned_to_name": _text(doc.get("assigned_to_name"), 160),
        "status": status,
        "is_open": not is_finished_status(doc.get("status")),
        "priority": _text(doc.get("priority"), 20),
        "progress": _progress(doc.get("progress")) if doc.get("progress") not in (None, "") else None,
        "due_date": _day(doc.get("due_date")),
        "repeat": _text(doc.get("repeat"), 20),
    }]


mirror = RecordMirror("task_records", COLLECTIONS, TaskRecord, TaskRecord.id, task_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's task_records rows match what its JSON tasks produce."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(TaskRecord).filter(TaskRecord.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(COLLECTIONS))):
        for row in task_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) the task_records table against the JSON tasks.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(COLLECTIONS)).distinct())]
        bad = [c for c in companies if not verify_company(db, c)]
        print(f"{len(companies)} companies checked, {len(bad)} differ")
        if args.rebuild and bad:
            for company_id in bad:
                mirror.rebuild_company(db, company_id)
            db.commit()
            print(f"rebuilt {len(bad)}")


if __name__ == "__main__":
    _main()
