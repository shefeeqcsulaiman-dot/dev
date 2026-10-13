"""Small lookup lists as real rows (the lookup_records table): the main copy of each entry.

Units, sales categories, service types, salespeople, shift setup, holidays and bank
accounts are short per-company lists. Each entry gets one row: code, name and status as
columns and every other field in `extra`, so the row holds the whole entry. Every write is
put through its row (canonical_lookup(), applied in models.py); like requests, the JSON is
left exactly as saved -- these lists feed many pickers that read the fields as they are.
Check or rebuild with `python -m app.lookup_records [--rebuild]`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, LookupRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("salesUnits", "salesCategories", "serviceTypes", "salesPeople", "rotaShifts", "hrHolidays", "bankAccounts")
FIELDS = (Field("code", 120), Field("name", 255), Field("status", 40))
_COMPARED = ("collection", "record_key", "code", "name", "status", "extra")


def canonical_lookup(record: dict[str, Any]) -> dict[str, Any]:
    """An entry as it is stored: put through its row and back (the same JSON)."""
    return record_from(fields_from(record, FIELDS), FIELDS)


def lookup_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = fields_from(doc, FIELDS)
    return [{
        "id": record_id, "company_id": company_id, "branch_id": branch_id, "collection": collection,
        "record_key": _text(record_key, 160),
        "code": fields["code"] or None, "name": fields["name"] or None, "status": fields["status"] or None,
        "extra": fields["extra"],
    }]


mirror = RecordMirror("lookup_records", COLLECTIONS, LookupRecord, LookupRecord.id, lookup_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """Rows match the records that still store their JSON; a record with no stored JSON (its
    row is the only copy, app/record_payloads.py) only needs a row."""
    stored_ids = {i for (i,) in session.query(AppDataRecord.id).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS), AppDataRecord._payload != "")}
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(LookupRecord).filter(LookupRecord.company_id == company_id)
            if r.id in stored_ids}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS))):
        if rec.id not in stored_ids:
            continue
        for row in lookup_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    blank = {i for (i,) in session.query(AppDataRecord.id).filter(
        AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS), AppDataRecord._payload == "")}
    with_rows = {i for (i,) in session.query(LookupRecord.id).filter(LookupRecord.id.in_(blank))} if blank else set()
    return have == want and blank == with_rows


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the lookup_records table.", COLLECTIONS, verify_company,
                         canonical_lookup, mirror)


if __name__ == "__main__":
    _main()
