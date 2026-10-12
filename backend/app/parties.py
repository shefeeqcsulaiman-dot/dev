"""Customers and vendors as real rows (the parties table): the main copy of each record.

Every customer / supplier has one row here with its fields as columns (name, TRN, email,
phone, address, emirate, category, contact, credit limit, status) and any other fields it
carries in `extra`, so the row alone holds the whole record. Saves go through it: the JSON
app-data record is regenerated from the row (party_record()), and kept only because many
generic readers (bootstrap, exports, backups, voice) still read app_data_records. Step A
made this a copy of the JSON; step B checks saves (app/party_checks.py); step C (this)
makes the row the source. Check, rebuild or rewrite with
`python -m app.parties [--rebuild] [--rewrite-json]`.

Values are normalised: TRN keeps its digits only, names get a lower-cased search key, the
credit limit is a number (dropped when it isn't one). A field the record has but leaves
empty is kept as "" (not dropped), so the regenerated record has the same keys.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, Party
from app.record_mirror import Record, RecordMirror

KINDS = {"customers": "customer", "vendors": "vendor"}


def _text(value: Any, size: int) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:size] or None


def _money(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or abs(number) >= Decimal("1e12"):
        return None
    return number.quantize(Decimal("0.01"))


# The record fields with a column of their own (app/main_copy.py does the rest).
FIELDS = (Field("name", 255, ("customer", "vendor")), Field("trn", 40, kind="digits"), Field("email", 255),
          Field("phone", 80, ("mobile",)), Field("address", 4000), Field("emirate", 80), Field("category", 120),
          Field("contact", 255, ("contact_person",)), Field("credit_limit", kind="money"), Field("status", 40))


def party_fields(doc: dict[str, Any]) -> dict[str, Any]:
    """Column values for one customer / supplier record, plus `extra` (the other fields)."""
    return fields_from(doc, FIELDS)


def party_record(fields: Any) -> dict[str, Any]:
    """The JSON record regenerated from a parties row (or party_fields() output)."""
    return record_from(fields, FIELDS)


def canonical_party(record: dict[str, Any]) -> dict[str, Any]:
    """A customer / supplier record as it is stored: put through its row and back."""
    return party_record(party_fields(record))


def party_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = party_fields(doc)
    name = fields["name"] or ""
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "kind": KINDS[collection],
        "record_key": _text(record_key, 160),
        **fields,
        "name": name,
        "name_key": name.lower(),
    }]


mirror = RecordMirror("parties", KINDS, Party, Party.id, party_rows).register()


_COMPARED = ("kind", "record_key", "name", "name_key", *(f.name for f in FIELDS if f.name != "name"), "extra")
_SOURCE = (AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload,
           AppDataRecord.branch_id, AppDataRecord.record_key)


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's parties rows match its records: every column, extra included."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(Party).filter(Party.company_id == company_id)}
    want = {}
    for rec in session.execute(select(*_SOURCE).where(AppDataRecord.company_id == company_id,
                                                      AppDataRecord.collection.in_(KINDS))):
        for row in party_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def stale_json(session: Session, company_id: str) -> list[str]:
    return main_copy.stale_json(session, company_id, KINDS, canonical_party)


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    main_copy.rewrite_json(session, record_ids, canonical_party)


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the parties table and the JSON it regenerates.", KINDS, verify_company, canonical_party, mirror)


if __name__ == "__main__":
    _main()
