"""Step D for documents: their JSON isn't stored (app/record_payloads.py). A document is
rebuilt exactly from its document_records row and its document_lines rows -- a date as
typed, an amount past 4 decimals, an empty text, each line as saved -- and the reads that
searched the JSON text find the same documents."""
import json
from uuid import uuid4

from sqlalchemy import text

from app.models import AppDataRecord, DocumentLine
from tests.test_document_lines import tenant  # noqa: F401  (fixture)


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _record(db, company_id, collection, key):
    db.expire_all()
    return db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == collection,
                                          AppDataRecord.record_key == key).one()


def _raw(db, record_id):
    return db.execute(text("SELECT payload FROM app_data_records WHERE id = :i"), {"i": record_id}).scalar()


def test_invoice_rebuilt_exactly_from_row_and_lines(client, db, tenant):  # noqa: F811
    cid, h = tenant
    no = f"INV-{uuid4().hex[:6]}"
    invoice = {"invoice_no": no, "customer": "Gulf Co", "date": "13/10/2026", "status": "", "subtotal": 1904.76,
               "vat_amount": 95.2380952, "total": 2000, "notes": "deliver Sunday",
               "lines": [{"description": "Widget", "qty": 2, "unit_price": 952.38, "amount": 1904.76, "tag": "x"},
                         {"description": "Free sample", "qty": 1, "unit_price": 0, "amount": 0}]}
    _save(client, h, "salesInvoices", invoice)
    rec = _record(db, cid, "salesInvoices", no)
    assert _raw(db, rec.id) == ""
    assert json.loads(rec.payload) == invoice
    assert db.query(DocumentLine).filter(DocumentLine.record_id == rec.id).count() == 2

    # Edit: one line fewer; still not stored as JSON, lines replaced.
    edited = {**invoice, "lines": invoice["lines"][:1], "status": "Paid"}
    _save(client, h, "salesInvoices", edited)
    rec = _record(db, cid, "salesInvoices", no)
    assert _raw(db, rec.id) == "" and json.loads(rec.payload) == edited
    assert db.query(DocumentLine).filter(DocumentLine.record_id == rec.id).count() == 1

    # The sales register lists it with its lines.
    listed = client.get("/api/v1/app-data/registers/salesInvoices", headers=h, params={"kind": "all", "limit": 200}).json()
    found = [r for r in listed["records"] if r.get("invoice_no") == no]
    assert found and found[0]["lines"] == edited["lines"]


def test_other_line_lists_and_searches(client, db, tenant):  # noqa: F811
    cid, h = tenant
    tag = uuid4().hex[:6]
    bill_no = f"B-{tag}"
    _save(client, h, "bills", {"bill_no": bill_no, "vendor": "Delta", "date": "2026-10-13", "total": 500,
                               "lines": [{"description": f"Paper {tag}", "qty": 5, "price": 100}]})
    # An expense's items (not a line collection) stay in the row's extra.
    _save(client, h, "expenses", {"ref": f"EX-{tag}", "vendor": "Taxi", "date": "2026-10-13", "total": 40,
                                  "items": [{"description": f"Ride {tag}", "amount": 40}]})
    _save(client, h, "payments", {"ref": f"PAY-{tag}", "contact": "Delta", "type": "Supplier Payment",
                                  "date": "2026-10-14", "amount": 500, "allocations": [{"doc_ref": bill_no, "amount": 500}]})
    for collection, key in (("bills", bill_no), ("expenses", f"EX-{tag}"), ("payments", f"PAY-{tag}")):
        rec = _record(db, cid, collection, key)
        assert _raw(db, rec.id) == "", collection
    # The payment (saved in the same flush as its row) settles the bill.
    assert float(_record(db, cid, "bills", bill_no).amount_paid) == 500
    expense = json.loads(_record(db, cid, "expenses", f"EX-{tag}").payload)
    assert expense["items"] == [{"description": f"Ride {tag}", "amount": 40}]

    # "Is this bill paid?" -- the payments register searched anywhere in the saved payment.
    r = client.get("/api/v1/app-data/registers/payments", headers=h, params={"kind": "supplier", "contains": bill_no, "limit": 5})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    # Product filter: a bill's line (document_lines) and an expense's item (extra).
    r = client.get("/api/v1/app-data/registers/bills", headers=h, params={"kind": "all", "product": f"paper {tag}"})
    assert r.json()["total"] == 1
    r = client.get("/api/v1/app-data/registers/expenses", headers=h, params={"kind": "all", "product": f"Ride {tag}"})
    assert r.json()["total"] == 1


def test_pos_receipt_rebuilt_with_its_items(client, db, tenant):  # noqa: F811
    cid, h = tenant
    no = f"R-{uuid4().hex[:6]}"
    sale = {"id": no, "receipt_no": no, "date": "2026-10-14", "customer": "Walk-In Customer", "staff": "Sara",
            "items": [{"name": "Tea", "qty": 2, "price": 3.5, "vatRate": 5, "line_total": 7, "line_vat": 0.35}],
            "subtotal": 7, "vat": 0.35, "total": 7.35, "payment_method": "cash", "cash_tendered": 10, "change": 2.65,
            "card_ref": "", "created_at": "2026-10-14T08:00:00.000Z"}
    _save(client, h, "posSales", sale)
    rec = _record(db, cid, "posSales", no)
    assert _raw(db, rec.id) == ""
    assert json.loads(rec.payload) == sale
