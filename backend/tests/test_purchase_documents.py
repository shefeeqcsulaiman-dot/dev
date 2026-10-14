"""Uploaded purchase documents live in purchase_documents (app/purchase_documents.py): their
JSON isn't stored; the list is built from the columns (no files read) and one document
comes back whole, file included."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.models import AppDataRecord, PurchaseDocumentRecord
from app.purchase_documents import mirror, verify_company
from tests.test_document_lines import tenant  # noqa: F401  (fixture)


def _doc(tag, **more):
    return {"id": f"PD-{tag}", "name": f" scan {tag}.pdf", "size": 2048, "type": "application/pdf",
            "base64": "data:application/pdf;base64,JVBERi0xLjQK", "category": "Purchase Invoices", "period": "2026-10",
            "status": "Extracted", "invoices": [{"invoice_no": "INV-9", "total": 105}], "savedInvoiceNos": ["INV-9"],
            "uploadedAt": "2026-10-14T07:00:00.000Z", "extractedAt": "", **more}


def test_saved_in_the_table_and_read_back(client, db, tenant):  # noqa: F811
    cid, h = tenant
    tag = uuid4().hex[:6]
    doc = _doc(tag)
    r = client.post("/api/v1/app-data?action=save", headers=h, json={"collection": "purchaseDocuments", "record": doc})
    assert r.status_code == 200, r.text
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.record_key == doc["id"]).one()
    assert db.execute(text("SELECT payload FROM app_data_records WHERE id = :i"), {"i": rec.id}).scalar() == ""
    row = db.get(PurchaseDocumentRecord, rec.id)
    assert (row.name, row.file_type, float(row.size), row.file) == ("scan " + tag + ".pdf", "application/pdf", 2048,
                                                                     doc["base64"])

    listed = client.get("/api/v1/app-data/purchase-documents", headers=h).json()["records"]
    mine = [d for d in listed if d.get("id") == doc["id"]]
    assert mine and "base64" not in mine[0]
    assert {k: v for k, v in mine[0].items() if k != "_v"} == {k: v for k, v in doc.items() if k != "base64"}

    one = client.get(f"/api/v1/app-data/purchase-documents/{doc['id']}", headers=h).json()["record"]
    assert {k: v for k, v in one.items() if k != "_v"} == doc
    assert verify_company(db, cid)
    with pytest.raises(RuntimeError, match="only copy"):
        mirror.rebuild_company(db, cid)

    # Delete removes record and row.
    record_id = rec.id
    r = client.post("/api/v1/app-data?action=delete", headers=h, json={"collection": "purchaseDocuments", "record": doc})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(PurchaseDocumentRecord, record_id) is None
