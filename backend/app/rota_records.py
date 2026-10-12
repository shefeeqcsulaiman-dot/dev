"""Rota assignments as real rows (the rota_records table), step A of moving them out of JSON.

Every save, edit and delete of a "rotaAssignments" app-data record updates its row in
rota_records (app/record_mirror.py). The JSON record is still the source of truth; the
rows can be checked or rebuilt with `python -m app.rota_records [--rebuild]`. Reads that
need an employee's days (days off, unpaid breaks, the ESS rota) find them through the
indexed (company, employee, day) columns instead of decoding the company's whole rota
for the range; the next steps (docs/scaling-plan-10k.md) move the rest.

Values are normalised on the way in: day is YYYY-MM-DD or empty, code is upper case,
kind is shift / off / leave / holiday (rota_kind), break_minutes is the assignment's own
break (empty when it has none, so callers can fall back to the shift's).
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, RotaRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("rotaAssignments",)
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPARED = ("record_key", "employee_no", "employee_name", "department", "location", "day", "kind", "code",
             "shift_type", "start_time", "end_time", "break_minutes", "status")


def rota_kind(rec: dict) -> str:
    """off / leave / holiday from the assignment's mark or code; anything else is a shift."""
    mark = str(rec.get("mark") or rec.get("type") or "").lower()
    code = str(rec.get("code") or "").upper()
    if mark == "off" or code == "OFF":
        return "off"
    if mark == "leave" or code == "L":
        return "leave"
    if mark == "holiday" or code == "PH":
        return "holiday"
    return "shift"


def minutes(value: Any) -> float | None:
    try:
        return max(0.0, float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> str | None:
    day = str(value or "").strip()[:10]
    return day if _ISO_DAY.match(day) else None


def rota_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    employee = doc.get("employee_id")
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        "employee_no": _text(employee, 80) if isinstance(employee, str) else None,
        "employee_name": _text(doc.get("employee_name"), 160),
        "department": _text(doc.get("department"), 120),
        "location": _text(doc.get("location"), 120),
        "day": _day(doc.get("date")),
        "kind": rota_kind(doc),
        "code": (_text(doc.get("code"), 20) or "").upper() or None,
        "shift_type": _text(doc.get("type"), 80),
        "start_time": _text(doc.get("start"), 8),
        "end_time": _text(doc.get("end"), 8),
        "break_minutes": minutes(doc.get("break_minutes", doc.get("breakMinutes"))),
        "status": _text(doc.get("status"), 40),
    }]


mirror = RecordMirror("rota_records", COLLECTIONS, RotaRecord, RotaRecord.id, rota_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's rota_records rows match what its JSON rota produces."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(RotaRecord).filter(RotaRecord.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(COLLECTIONS))):
        for row in rota_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) the rota_records table against the JSON rota.")
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
