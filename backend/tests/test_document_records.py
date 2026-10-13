"""document_records is the main copy of sales invoices' and purchases' headers (step C):
amounts become numbers at full precision, text and dates stay as typed, lines are kept."""
import json

from app import document_records
from app.document_records import canonical_document
from app.models import AppDataRecord, DocumentRecord
from tests.test_party_checks import _save, tenant  # noqa: F401  (fixture)


def test_round_trip_keeps_lines_and_precision():
    inv = {"invoice_no": "INV-1", "customer": " Acme ", "date": "20/02/2026", "subtotal": 95.23809523809524,
           "vat_amount": "4.76", "total": "1,000.00", "status": "Issued", "notes": "x", "lines": [{"qty": "2"}]}
    out = canonical_document("salesInvoices", inv)
    assert out == {"invoice_no": "INV-1", "customer": " Acme ", "date": "20/02/2026", "status": "Issued",
                   "subtotal": 95.23809523809524, "vat_amount": 4.76, "total": 1000.0, "notes": "x",
                   "lines": [{"qty": "2"}]}
    assert canonical_document("salesInvoices", out) == out
    pur = {"ref": "P-1", "supplier": "Gulf", "net_amount": "100", "tax_amount": "5", "total": "105", "paid": "n/a",
           "due": 105, "lines": [{"sku": "A"}]}
    assert canonical_document("purchaseRecords", pur) == {"ref": "P-1", "supplier": "Gulf", "net_amount": 100,
                                                          "tax_amount": 5, "total": 105, "paid": "n/a", "due": 105,
                                                          "lines": [{"sku": "A"}]}


def test_saves_store_the_regenerated_header(client, db, tenant):  # noqa: F811
    company_id, h = tenant
    assert _save(client, h, "salesInvoices", {"invoice_no": "INV-DR", "customer": "Acme", "date": "2026-10-12",
                                              "due_date": "12/11/2026", "subtotal": "1,000", "vat_amount": "50",
                                              "total": "1,050", "status": "Issued", "po_number": "PO-9",
                                              "lines": [{"description": "Widget", "qty": 1, "unit_price": 1000}]}).status_code == 200
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key == "INV-DR").one()
    row = db.get(DocumentRecord, rec.id)
    assert (row.number, row.party, row.doc_date, row.due_date, float(row.total), float(row.vat)) == \
           ("INV-DR", "Acme", "2026-10-12", "2026-11-12", 1050.0, 50.0)
    stored = json.loads(rec.payload)
    assert (stored["total"], stored["subtotal"], stored["po_number"], stored["lines"][0]["description"]) == (1050, 1000, "PO-9", "Widget")
    assert json.loads(row.extra) == {"po_number": "PO-9"}
    assert float(rec.fig_gross) == 1050.0 and rec.record_date == "2026-10-12"   # report columns unchanged
    assert document_records.verify_company(db, company_id) and document_records.stale_json(db, company_id) == []
