"""Approved expenses post to the ledger on their own date: Dr the category's expense account,
Dr Input VAT, Cr Supplier payable, with an input VAT line for the VAT return. Pending, draft
and rejected expenses don't post; deleting or un-approving one removes its posting; the P&L
summary counts each expense once."""
from datetime import date
from decimal import Decimal

from app.models import Account, JournalEntry, JournalLine, SourceTransaction, TaxLine
from tests.test_posting_document_date import _company, _save, _vat
from tests.test_registration import _register


def _expense(ref, day="2025-09-10", amount=200, vat=10, status="Approved", category="Utilities"):
    return {"ref": ref, "date": day, "category": category, "vendor": "DEWA", "description": "Power bill",
            "amount": amount, "vat_amount": vat, "total": amount + vat, "status": status}


def _expense_tx(db, cid, ref):
    db.expire_all()
    return db.query(SourceTransaction).filter(SourceTransaction.company_id == cid, SourceTransaction.module == "expense",
                                              SourceTransaction.reference == ref).one_or_none()


def _journal_lines(db, cid, tx):
    rows = (db.query(Account.code, JournalLine.debit, JournalLine.credit)
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
            .filter(JournalEntry.company_id == cid, JournalEntry.source_id == tx.id).all())
    return sorted((code, Decimal(d), Decimal(c)) for code, d, c in rows)


def _summary(client, h):
    r = client.get("/api/v1/reports/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_approved_expense_posts_on_its_date_with_input_vat(client, db):
    h = _register(client, "exp-post")
    cid = _company(client, h)
    _save(client, h, "expenses", _expense("EXP-1"))
    tx = _expense_tx(db, cid, "EXP-1")
    assert tx is not None and tx.status == "posted"
    assert tx.document_date == date(2025, 9, 10)
    assert _journal_lines(db, cid, tx) == [
        ("2100", Decimal("0.00"), Decimal("210.00")),
        ("2210", Decimal("10.00"), Decimal("0.00")),
        ("6200", Decimal("200.00"), Decimal("0.00")),
    ]
    journal = db.query(JournalEntry).filter(JournalEntry.source_id == tx.id).one()
    assert journal.entry_date.date() == date(2025, 9, 10)
    tax = db.query(TaxLine).filter(TaxLine.source_id == tx.id).one()
    assert (tax.direction, tax.period, tax.taxable_amount, tax.tax_amount) == ("input", "2025-09", Decimal("200.00"), Decimal("10.00"))
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("10.00"))

    # re-saving the same expense doesn't post it twice; an edit reposts the new amount
    _save(client, h, "expenses", _expense("EXP-1"))
    _save(client, h, "expenses", _expense("EXP-1", amount=300, vat=15))
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("15.00"))
    assert _expense_tx(db, cid, "EXP-1").total == Decimal("315.00")


def test_expense_account_follows_category(client, db):
    h = _register(client, "exp-acct")
    cid = _company(client, h)
    for ref, category in (("EXP-A1", "Supplies"), ("EXP-A2", "Rent"), ("EXP-A3", "Direct Expense"),
                          ("EXP-A4", "Bank Charges")):
        _save(client, h, "expenses", _expense(ref, category=category, vat=0))
    codes = {ref: [c for c, d, _ in _journal_lines(db, cid, _expense_tx(db, cid, ref)) if d][0]
             for ref in ("EXP-A1", "EXP-A2", "EXP-A3", "EXP-A4")}
    assert codes == {"EXP-A1": "6300", "EXP-A2": "6100", "EXP-A3": "5000", "EXP-A4": "6400"}
    # zero VAT still reports the supply (zero-rated) on the input side
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("0.00"))


def test_unapproved_expenses_do_not_post_and_unapproving_removes_posting(client, db):
    h = _register(client, "exp-status")
    cid = _company(client, h)
    _save(client, h, "expenses", _expense("EXP-S1", status="Pending"))
    _save(client, h, "expenses", _expense("EXP-S2", status="Draft"))
    assert _expense_tx(db, cid, "EXP-S1") is None and _expense_tx(db, cid, "EXP-S2") is None
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("0.00"))

    _save(client, h, "expenses", _expense("EXP-S1", status="Approved"))
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("10.00"))
    _save(client, h, "expenses", _expense("EXP-S1", status="Rejected"))
    assert _expense_tx(db, cid, "EXP-S1") is None
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("0.00"))
    assert db.query(JournalEntry).filter(JournalEntry.company_id == cid, JournalEntry.source_module == "expense").count() == 0


def test_deleting_an_expense_removes_its_posting(client, db):
    h = _register(client, "exp-del")
    cid = _company(client, h)
    _save(client, h, "expenses", _expense("EXP-D1"))
    tx_id = _expense_tx(db, cid, "EXP-D1").id
    r = client.post("/api/v1/app-data", headers=h, params={"action": "delete"},
                    json={"collection": "expenses", "record": {"ref": "EXP-D1"}})
    assert r.status_code == 200, r.text
    assert _expense_tx(db, cid, "EXP-D1") is None
    assert db.query(JournalEntry).filter(JournalEntry.source_id == tx_id).count() == 0
    assert db.query(TaxLine).filter(TaxLine.source_id == tx_id).count() == 0
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("0.00"))


def test_locked_period_refuses_expense_postings(client, db):
    h = _register(client, "exp-lock")
    cid = _company(client, h)
    _save(client, h, "expenses", _expense("EXP-L1", day="2025-06-05"))
    _save(client, h, "lockedPeriods", {"id": "2025-06", "locked": True})
    _save(client, h, "expenses", _expense("EXP-L2", day="2025-06-20"), status=423)
    # rejecting or moving a posted expense out of the locked month is refused too
    _save(client, h, "expenses", _expense("EXP-L1", day="2025-06-05", status="Rejected"), status=423)
    _save(client, h, "expenses", _expense("EXP-L1", day="2025-07-05"), status=423)
    assert _expense_tx(db, cid, "EXP-L1") is not None
    assert _expense_tx(db, cid, "EXP-L2") is None


def test_summary_counts_each_expense_once(client, db):
    h = _register(client, "exp-sum")
    base = Decimal(_summary(client, h)["profit_loss"]["other_expenses"])
    _save(client, h, "expenses", _expense("EXP-P1", amount=200, vat=10, status="Approved"))
    _save(client, h, "expenses", _expense("EXP-P2", amount=50, vat=0, status="Pending"))
    body = _summary(client, h)
    # approved: 200 from its posting (ex-VAT); pending: 50 from the record itself
    assert Decimal(body["profit_loss"]["other_expenses"]) - base == Decimal("250.00")
    assert Decimal(body["vat"]["input"]["input_vat"]) == Decimal("10.00")
    totals = body["balance_sheet"]["totals"]
    assert Decimal(totals["difference"]) == 0, totals


def test_paid_expense_is_settled_against_cash(client, db):
    h = _register(client, "exp-paid")
    cid = _company(client, h)
    _save(client, h, "expenses", _expense("EXP-C1", status="Paid"))
    pay = db.query(SourceTransaction).filter(SourceTransaction.company_id == cid, SourceTransaction.module == "payment",
                                             SourceTransaction.reference == "EXP-C1-PAID").one()
    assert pay.document_date == date(2025, 9, 10)
    assert _journal_lines(db, cid, pay) == [("1000", Decimal("0.00"), Decimal("210.00")), ("2100", Decimal("210.00"), Decimal("0.00"))]
    # one input VAT line only: the settlement carries no VAT
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("10.00"))

    # back to approved (unpaid): the settlement goes, the expense stays
    _save(client, h, "expenses", _expense("EXP-C1", status="Approved"))
    db.expire_all()
    assert db.query(SourceTransaction).filter(SourceTransaction.reference == "EXP-C1-PAID").count() == 0
    assert _expense_tx(db, cid, "EXP-C1") is not None

    _save(client, h, "expenses", _expense("EXP-C1", status="Paid"))
    r = client.post("/api/v1/app-data", headers=h, params={"action": "delete"},
                    json={"collection": "expenses", "record": {"ref": "EXP-C1"}})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(SourceTransaction).filter(SourceTransaction.company_id == cid).count() == 0
