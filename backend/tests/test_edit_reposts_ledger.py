"""Editing a saved (already posted) sales invoice or purchase must correct its General Ledger
(reversal + fresh journal) and VAT line — previously they stayed at the original amounts."""
from decimal import Decimal
from uuid import uuid4

from app.models import JournalEntry, JournalLine, TaxLine, SourceTransaction


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"}, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _posted(db, company_id, ref):
    db.expire_all()
    tx = db.query(SourceTransaction).filter(SourceTransaction.company_id == company_id, SourceTransaction.reference == ref).one()
    journals = db.query(JournalEntry).filter(JournalEntry.company_id == company_id, JournalEntry.source_id == tx.id).all()
    reversals = db.query(JournalEntry).filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "reversal",
                                              JournalEntry.source_id.in_([j.id for j in journals])).all()
    # Net debit still standing: posted journals minus the ones reversed (originals are never edited).
    net = sum((l.debit for j in journals for l in db.query(JournalLine).filter(JournalLine.journal_id == j.id).all()), Decimal("0"))
    net -= sum((l.credit for r in reversals for l in db.query(JournalLine).filter(JournalLine.journal_id == r.id).all()), Decimal("0"))
    taxes = db.query(TaxLine).filter(TaxLine.company_id == company_id, TaxLine.source_id == tx.id).all()
    return len(journals) - len(reversals), net, [(t.taxable_amount, t.tax_amount) for t in taxes]


def test_edited_sales_invoice_reposts(client, db, auth_headers):
    cid = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    ref = f"EDIT-SI-{uuid4().hex[:6]}"
    base = {"invoice_no": ref, "customer": "Edit Customer", "date": "2026-10-01", "status": "Issued"}
    _save(client, auth_headers, "salesInvoices", {**base, "lines": [{"description": "x", "qty": 1, "unit_price": 100, "vat_rate": 5}]})
    assert _posted(db, cid, ref) == (1, Decimal("105.00"), [(Decimal("100.00"), Decimal("5.00"))])
    _save(client, auth_headers, "salesInvoices", {**base, "lines": [{"description": "x", "qty": 1, "unit_price": 200, "vat_rate": 5}]})
    assert _posted(db, cid, ref) == (1, Decimal("210.00"), [(Decimal("200.00"), Decimal("10.00"))])
    # unchanged re-save keeps the single posting
    _save(client, auth_headers, "salesInvoices", {**base, "lines": [{"description": "x", "qty": 1, "unit_price": 200, "vat_rate": 5}]})
    assert _posted(db, cid, ref) == (1, Decimal("210.00"), [(Decimal("200.00"), Decimal("10.00"))])


def test_edited_purchase_reposts(client, db, auth_headers):
    cid = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    ref = f"EDIT-PU-{uuid4().hex[:6]}"
    rec = {"ref": ref, "supplier": "Edit Supplier", "date": "2026-10-01"}
    _save(client, auth_headers, "purchaseRecords", {**rec, "subtotal": 100, "vat_amount": 5, "total": 105, "lines": [{"product": "p", "quantity": 1, "unit_cost": 100, "line_total": 100}]})
    assert _posted(db, cid, ref) == (1, Decimal("105.00"), [(Decimal("100.00"), Decimal("5.00"))])
    _save(client, auth_headers, "purchaseRecords", {**rec, "subtotal": 300, "vat_amount": 15, "total": 315, "lines": [{"product": "p", "quantity": 3, "unit_cost": 100, "line_total": 300}]})
    assert _posted(db, cid, ref) == (1, Decimal("315.00"), [(Decimal("300.00"), Decimal("15.00"))])


def test_deleting_an_edited_invoice_leaves_no_ledger_behind(client, db, auth_headers):
    from app.models import Account
    cid = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    ref = f"EDIT-DEL-{uuid4().hex[:6]}"
    base = {"invoice_no": ref, "customer": "Edit Del", "date": "2026-10-01", "status": "Issued"}
    _save(client, auth_headers, "salesInvoices", {**base, "lines": [{"description": "x", "qty": 1, "unit_price": 100, "vat_rate": 5}]})
    _save(client, auth_headers, "salesInvoices", {**base, "lines": [{"description": "x", "qty": 1, "unit_price": 200, "vat_rate": 5}]})
    r = client.post("/api/v1/app-data?action=delete", headers=auth_headers, json={"collection": "salesInvoices", "record": {"invoice_no": ref}})
    assert r.status_code == 200, r.text
    db.expire_all()
    left = db.query(JournalEntry).filter(JournalEntry.company_id == cid, JournalEntry.entry_number.in_([f"AUTO-{ref}", f"REV-AUTO-{ref}"])).count()
    assert left == 0
