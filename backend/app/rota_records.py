"""Rota assignments as real rows (the rota_records table): the main copy of each assignment.

Every rota assignment has one row here: employee, day, kind (shift / off / leave / holiday,
rota_kind()), code, shift type, times, break and status as columns, and its other fields
(employee's role, the cell's class, notes, attached tasks...) in `extra`, so the row holds
the whole assignment. Every write, from any path, is put through its row and the JSON
regenerated from it (canonical_rota(), applied in models.py), kept because generic readers
still read app_data_records. Days off, unpaid breaks and the ESS rota read the rows through
the (company, employee, day) index. Check, rebuild or rewrite with
`python -m app.rota_records [--rebuild] [--rewrite-json]`.

Normalised (app/main_copy.py): the date is YYYY-MM-DD, break_minutes a number (dropped when
it isn't one); text keeps its original value in the JSON. Columns: code upper case, an
empty text field NULL.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.main_copy import Field, fields_from, minutes, record_from  # noqa: F401 -- minutes: used by rota_days.py
from app.models import AppDataRecord, RotaRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("rotaAssignments",)
# The record's field names and, where it differs, the column (app/main_copy.py).
FIELDS = (Field("employee_id", 80, column="employee_no"), Field("employee_name", 160), Field("department", 120),
          Field("location", 120), Field("date", kind="day", column="day"), Field("code", 20),
          Field("type", 80, column="shift_type"), Field("start", 8, column="start_time"),
          Field("end", 8, column="end_time"), Field("break_minutes", kind="minutes", aliases=("breakMinutes",)),
          Field("status", 40))
_COMPARED = ("record_key", "employee_no", "employee_name", "department", "location", "day", "kind", "code",
             "shift_type", "start_time", "end_time", "break_minutes", "status", "extra")


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


def rota_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return fields_from(doc, FIELDS)


def canonical_rota(record: dict[str, Any]) -> dict[str, Any]:
    """A rota assignment as it is stored: put through its row and back."""
    return record_from(rota_fields(record), FIELDS)


def rota_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = rota_fields(doc)
    row = {col: (value or None) if isinstance(value, str) else value for col, value in fields.items()}
    row["extra"] = fields["extra"]
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        **row,
        "code": (row["code"] or "").upper() or None,
        "kind": rota_kind(doc),
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


def stale_json(session: Session, company_id: str) -> list[str]:
    return main_copy.stale_json(session, company_id, COLLECTIONS, canonical_rota)


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    main_copy.rewrite_json(session, record_ids, canonical_rota)


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the rota_records table and the JSON it regenerates.", COLLECTIONS,
                         verify_company, canonical_rota, mirror)


if __name__ == "__main__":
    _main()
