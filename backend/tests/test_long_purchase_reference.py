"""A purchase whose reference carries a long supplier name saves and posts: the journal
entry is "AUTO-<reference>", which overflowed journal_entries.entry_number (40) on
PostgreSQL (2026-10-10, "value too long for type character varying(40)")."""
from app.models import GeneralLedgerEntry, JournalEntry, SourceTransaction, StockMovement, Voucher


def test_number_and_reference_columns_fit_long_supplier_references():
    ref = "INV/2026/02964 (Eurovets Veterinary Medicines L.L.C.)"
    assert JournalEntry.__table__.c.entry_number.type.length >= len("REV-AUTO-" + ref)
    assert Voucher.__table__.c.voucher_no.type.length >= len("REV-AUTO-" + ref)
    assert GeneralLedgerEntry.__table__.c.voucher_no.type.length >= len("REV-AUTO-" + ref)
    long_ref = "INV/2026/02964 (" + "A Very Long Supplier Trading Name L.L.C. " * 3 + ")"
    assert SourceTransaction.__table__.c.reference.type.length >= len(long_ref)
    assert StockMovement.__table__.c.reference.type.length >= len("PUR-" + long_ref)


def test_saving_purchase_with_long_reference_posts_its_journal(client, auth_headers, db):
    from uuid import uuid4

    ref = f"INV/2026/{uuid4().hex[:5]} (Eurovets Veterinary Medicines L.L.C.)"
    record = {"id": f"PU-{uuid4().hex[:8]}", "ref": ref, "invoice_no": ref, "supplier": "Eurovets Veterinary Medicines L.L.C.",
              "date": "2026-10-10", "net_amount": "100.00", "tax_amount": "5.00", "total": "105.00",
              "lines": [{"product": "Vaccine", "qty": 1, "unit_cost": "100.00"}]}
    r = client.post("/api/v1/app-data?action=bulk-save", headers=auth_headers,
                    json={"collection": "purchaseRecords", "records": [record]})
    assert r.status_code == 200, r.text
    assert db.query(JournalEntry).filter(JournalEntry.entry_number == f"AUTO-{ref}").count() == 1
