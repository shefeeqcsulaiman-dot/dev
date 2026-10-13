"""Documents as real rows (the document_records table): the main copy of each document's
header -- sales invoices, purchase records, bills, quotations, expenses and payments.

One row per document with its header as columns (number, customer / supplier / contact, TRN,
date, due date, status, subtotal, VAT, total, paid, salesperson / owner / location)
and the header's other fields in `extra`. The line items are left out of the row: they stay
in the JSON, and have their own typed copy in document_lines (app/doc_lines.py), so copying
them here would store every invoice's lines a third time. Every write, from any path, is put
through the row and the JSON regenerated from it (canonical_document(), applied in
models.py), kept because generic readers still read app_data_records. Check, rebuild or
rewrite with `python -m app.document_records [--rebuild] [--rewrite-json]`.

Normalised (app/main_copy.py): amounts become numbers at full precision ("1,234.50" ->
1234.5; 95.2380952 stays, so VAT and totals don't move; one that isn't a number is kept as
typed); text and dates keep their original value in the JSON. Columns: dates YYYY-MM-DD
(parsed the way reports read them, app/doc_index.py), amounts to 4 decimals.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.doc_index import parse_document_date
from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, DocumentRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror

LINES = ("lines", "items")  # kept only in the JSON (and document_lines)
# The record's field names per collection, onto shared columns.
FIELDS = {
    "salesInvoices": (
        Field("invoice_no", 160, column="number"), Field("customer", 255, ("customer_name",), column="party"),
        Field("customer_trn", 80, column="party_trn"), Field("date", 40, ("invoice_date",), column="doc_date"),
        Field("due_date", 40), Field("status", 40), Field("subtotal", kind="amount"),
        Field("vat_amount", kind="amount", aliases=("vat",), column="vat"), Field("total", kind="amount"),
        Field("salesperson", 120),
    ),
    "purchaseRecords": (
        Field("ref", 160, column="number"), Field("supplier", 255, ("vendor", "supplier_name"), column="party"),
        Field("supplier_trn", 80, column="party_trn"), Field("date", 40, ("invoice_date", "purchase_date"), column="doc_date"),
        Field("due_date", 40), Field("status", 40), Field("net_amount", kind="amount", aliases=("subtotal",), column="subtotal"),
        Field("tax_amount", kind="amount", aliases=("vat_amount", "vat"), column="vat"), Field("total", kind="amount"),
        Field("paid", kind="amount", aliases=("paid_amount",)), Field("location", 120),
    ),
    # Field names as app/doc_index.py SPECS reads them for these documents.
    "bills": (
        Field("bill_no", 160, column="number"), Field("vendor", 255, ("supplier", "vendor_name"), column="party"),
        Field("vendor_trn", 80, ("supplier_trn",), column="party_trn"), Field("date", 40, ("bill_date",), column="doc_date"),
        Field("due", 40, ("due_date",), column="due_date"), Field("status", 40), Field("subtotal", kind="amount"),
        Field("vat", kind="amount", aliases=("vat_amount", "tax_amount")), Field("total", kind="amount", aliases=("grand_total",)),
        Field("paid", kind="amount", aliases=("paid_amount",)),
    ),
    "quotations": (
        Field("quote_no", 160, column="number"), Field("customer", 255, ("customer_name",), column="party"),
        Field("customer_trn", 80, column="party_trn"), Field("date", 40, column="doc_date"),
        Field("valid_until", 40, ("expiry_date",), column="due_date"), Field("status", 40), Field("subtotal", kind="amount"),
        Field("vat_amount", kind="amount", aliases=("vat",), column="vat"), Field("total", kind="amount"),
        Field("owner", 120, column="salesperson"),
    ),
    "expenses": (
        Field("ref", 160, column="number"), Field("vendor", 255, ("supplier", "employee"), column="party"),
        Field("vendor_trn", 80, ("supplier_trn",), column="party_trn"), Field("date", 40, ("expense_date",), column="doc_date"),
        Field("status", 40), Field("subtotal", kind="amount", aliases=("net_amount",)),
        Field("vat", kind="amount", aliases=("vat_amount", "tax_amount")), Field("total", kind="amount", aliases=("amount",)),
    ),
    "payments": (
        Field("ref", 160, column="number"), Field("contact", 255, ("customer", "supplier", "vendor"), column="party"),
        Field("date", 40, column="doc_date"), Field("status", 40), Field("amount", kind="amount", aliases=("total",), column="total"),
    ),
}
COLLECTIONS = tuple(FIELDS)
_DAYS = ("doc_date", "due_date")
_AMOUNTS = ("subtotal", "vat", "total", "paid")
_COLUMNS = ("number", "party", "party_trn", "doc_date", "due_date", "status", "subtotal", "vat", "total", "paid",
            "salesperson", "location")
_COMPARED = ("collection", "record_key", *_COLUMNS, "extra")
_FOUR = Decimal("0.0001")


def document_fields(collection: str, doc: dict[str, Any]) -> dict[str, Any]:
    return fields_from(doc, FIELDS[collection], skip=LINES)


def canonical_document(collection: str, record: dict[str, Any]) -> dict[str, Any]:
    """A sales invoice / purchase as it is stored: its header put through its row and back,
    its lines (and any other line list) kept as they are."""
    out = record_from(document_fields(collection, record), FIELDS[collection])
    for key in LINES:
        if key in record:
            out[key] = record[key]
    return out


def _column(col: str, value: Any) -> Any:
    if col in _DAYS:
        parsed = parse_document_date(value) if value not in (None, "") else None
        return parsed.isoformat() if parsed else None
    if col in _AMOUNTS:
        return Decimal(str(value)).quantize(_FOUR) if value is not None and abs(value) < 1e12 else None
    return value or None  # empty text: NULL


def document_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = document_fields(collection, doc)
    row = {col: _column(col, fields.get(col)) for col in _COLUMNS}
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "collection": collection,
        "record_key": _text(record_key, 160),
        **row,
        "extra": fields["extra"],
    }]


mirror = RecordMirror("document_records", COLLECTIONS, DocumentRecord, DocumentRecord.id, document_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's document_records rows match its sales invoices / purchases."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(DocumentRecord).filter(DocumentRecord.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS))):
        for row in document_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def stale_json(session: Session, company_id: str) -> list[str]:
    stale = []
    for collection in COLLECTIONS:
        stale += main_copy.stale_json(session, company_id, (collection,),
                                      lambda doc, c=collection: canonical_document(c, doc))
    return stale


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    rows = session.query(AppDataRecord.id, AppDataRecord.collection).filter(AppDataRecord.id.in_(record_ids)).all()
    for collection in COLLECTIONS:
        ids = [i for i, c in rows if c == collection]
        if ids:
            main_copy.rewrite_json(session, ids, lambda doc, c=collection: canonical_document(c, doc))


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) the document_records table and the JSON it regenerates.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ")
    parser.add_argument("--rewrite-json", action="store_true", help="regenerate records whose JSON isn't canonical")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(COLLECTIONS)).distinct())]
        bad = [c for c in companies if not verify_company(db, c)]
        stale = {c: ids for c in companies if (ids := stale_json(db, c))}
        print(f"{len(companies)} companies checked, {len(bad)} with rows that differ, "
              f"{sum(map(len, stale.values()))} records with non-canonical JSON")
        if args.rewrite_json and stale:
            for ids in stale.values():
                rewrite_json(db, ids)
            db.commit()
        if args.rebuild and bad:
            for company_id in bad:
                mirror.rebuild_company(db, company_id)
            db.commit()


if __name__ == "__main__":
    _main()
