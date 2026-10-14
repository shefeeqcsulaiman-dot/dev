"""Uploaded purchase documents as real rows (the purchase_documents table): the only copy.

Each file uploaded on the Purchases screen ("purchaseDocuments") has one row: its name,
type, size, category, period, status and upload / extraction times as columns, the file
itself (base64) in `file`, and everything else -- the invoices read from it, which of them
were saved -- in `extra`. Its JSON isn't stored (app/record_payloads.py): it is rebuilt
from the row on read; the list endpoint reads the columns without the file. Text keeps its
original value (app/main_copy.py: a column trimmed or cut short keeps the original in extra).
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, PurchaseDocumentRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("purchaseDocuments",)
FILE = "base64"  # its own column
FIELDS = (Field("name", 255), Field("type", 120, column="file_type"), Field("size", kind="number"),
          Field("category", 120), Field("period", 40), Field("status", 40),
          Field("uploadedAt", 40, column="uploaded_at"), Field("extractedAt", 40, column="extracted_at"))
_COMPARED = ("record_key", "name", "file_type", "size", "category", "period", "status", "uploaded_at",
             "extracted_at", "extra", "file")


def document_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = fields_from(doc, FIELDS, skip=(FILE,))
    file = doc.get(FILE)
    if FILE in doc and not isinstance(file, str):  # not a file: kept as given
        extra = json.loads(fields["extra"] or "{}")
        extra[FILE] = file
        fields["extra"], file = json.dumps(extra, ensure_ascii=False, default=str), None
    return [{"id": record_id, "company_id": company_id, "branch_id": branch_id, "record_key": _text(record_key, 160),
             **fields, "file": file if isinstance(file, str) else None}]


def rebuilt_document(row: Any, with_file: bool = True) -> dict[str, Any]:
    """The JSON record rebuilt from its row (without the file: for lists)."""
    record = record_from(row, FIELDS)
    if with_file and row.file is not None:
        record[FILE] = row.file
    return record


mirror = RecordMirror("purchase_documents", COLLECTIONS, PurchaseDocumentRecord, PurchaseDocumentRecord.id,
                      document_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """Rows match the documents that still store their JSON; one with no stored JSON (its row
    is the only copy) only needs a row."""
    stored, blank, want = set(), set(), {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord._payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(COLLECTIONS))):
        if not rec[3]:
            blank.add(rec[0])
            continue
        stored.add(rec[0])
        for row in document_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    table = PurchaseDocumentRecord.__table__
    rows = session.execute(select(table).where(table.c.company_id == company_id)).all()
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED) for r in rows if r.id in stored}
    return have == want and blank <= {r.id for r in rows}
