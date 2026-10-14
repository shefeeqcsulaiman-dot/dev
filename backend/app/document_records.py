"""Documents as real rows (the document_records table): the main copy of each document's
header -- sales invoices, purchase records, bills, quotations, expenses and payments.

One row per document with its header as columns (number, customer / supplier / contact, TRN,
date, due date, status, subtotal, VAT, total, paid, salesperson / owner / location)
and the header's other fields in `extra` -- with any value a column can't give back exactly
(a date as typed, an amount past 4 decimals, an empty text), as saved. The "lines" of sales
invoices, quotations, bills and purchases are one row each in document_lines
(app/doc_lines.py), each line kept exactly; extra marks that the document has them
("~lines"). Any other line list stays in extra. Every write, from any path, is put through
the row and the JSON regenerated from it (canonical_document(), applied in models.py); the
JSON itself isn't stored (app/record_payloads.py) but rebuilt from the row and its lines
(rebuilt_document()). Check, rewrite with
`python -m app.document_records [--rewrite-json]`.

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
from app.doc_lines import LINE_COLLECTIONS
from app.parties import _text
from app.record_mirror import Record, RecordMirror

LINES = ("lines", "items")  # not header fields: see document_rows()
_LINES_MARK = "~lines"  # extra: the document's "lines" are its document_lines rows
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
    # POS receipts (pos.html): their "items" stay in extra, exactly as saved.
    "posSales": (
        Field("receipt_no", 160, column="number"), Field("customer", 255, column="party"),
        Field("date", 40, column="doc_date"), Field("status", 40), Field("subtotal", kind="amount"),
        Field("vat", kind="amount"), Field("total", kind="amount"), Field("staff", 120, column="salesperson"),
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


def _from_column(value: Any) -> Any:
    """A column's value as the rebuilt record has it (app/main_copy.py record_from())."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def lines_in_table(collection: str, doc: dict[str, Any]) -> bool:
    """Its "lines" are kept as document_lines rows (a list of line objects, in a collection
    with lines); any other line list stays in extra."""
    lines = doc.get("lines")
    return collection in LINE_COLLECTIONS and isinstance(lines, list) and all(isinstance(l, dict) for l in lines)


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
    extra = json.loads(fields["extra"]) if fields["extra"] else {}
    # A value its column can't give back exactly (a date as typed, an amount past 4
    # decimals, an empty text) is kept in extra as the record has it.
    for field in FIELDS[collection]:
        want = fields[field.col]
        if want is not None and field.name not in extra and _from_column(row.get(field.col)) != want:
            extra[field.name] = want
    for key in LINES:
        if key in doc:
            if key == "lines" and lines_in_table(collection, doc):
                extra[_LINES_MARK] = True
            else:
                extra[key] = doc[key]
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "collection": collection,
        "record_key": _text(record_key, 160),
        **row,
        "extra": json.dumps(extra, ensure_ascii=False, default=str) if extra else None,
    }]


class StoredDocument:
    """A document's row with its lines (each as saved, in order): all its JSON is rebuilt from.
    lines None: not loaded -- the record is rebuilt without them (header-only readers)."""
    __slots__ = ("row", "lines")

    def __init__(self, row: Any, lines: list[Any] | None):
        self.row, self.lines = row, lines


def rebuilt_document(stored: StoredDocument) -> dict[str, Any]:
    record = record_from(stored.row, FIELDS[stored.row.collection])
    if record.pop(_LINES_MARK, None) and stored.lines is not None:
        record["lines"] = stored.lines
    return record


def typed_documents(session: Session, records: list[Any], with_lines: bool = True) -> dict[str, StoredDocument]:
    """{record id: StoredDocument} for these records: one query for the rows, one for the
    lines (plain column rows, not ORM objects: app/record_payloads.py _typed_rows()).
    with_lines False: headers only (no lines query; rebuilt records have no "lines")."""
    from app.models import DocumentLine
    from app.record_payloads import id_batches

    table = DocumentRecord.__table__
    ids = [r.id for r in records]
    rows: dict[str, Any] = {}
    lines: dict[str, list[Any]] = {}
    for match in id_batches(session, table.c.id, ids):
        for row in session.execute(select(table).where(match)):
            rows[row.id] = row
    for match in (id_batches(session, DocumentLine.record_id, ids) if with_lines else ()):
        for record_id, data in session.execute(
                select(DocumentLine.record_id, DocumentLine.data).where(match)
                .order_by(DocumentLine.record_id, DocumentLine.line_no)):
            try:
                lines.setdefault(record_id, []).append(json.loads(data) if data else None)
            except (TypeError, ValueError):
                lines.setdefault(record_id, []).append(None)
    return {record_id: StoredDocument(row, lines.get(record_id, []) if with_lines else None)
            for record_id, row in rows.items()}


mirror = RecordMirror("document_records", COLLECTIONS, DocumentRecord, DocumentRecord.id, document_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """Rows match the documents that still store their JSON; one with no stored JSON (its
    row is the only copy, app/record_payloads.py) only needs a row."""
    stored, blank, want = set(), set(), {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord._payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS))):
        if not rec[3]:
            blank.add(rec[0])
            continue
        stored.add(rec[0])
        for row in document_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    rows = session.query(DocumentRecord).filter(DocumentRecord.company_id == company_id).all()
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED) for r in rows if r.id in stored}
    return have == want and blank <= {r.id for r in rows}


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

    parser = argparse.ArgumentParser(description="Check the document_records table and the JSON it regenerates.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ (refused "
                                                                "once the JSON isn't stored)")
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
