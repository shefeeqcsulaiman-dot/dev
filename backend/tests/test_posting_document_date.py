"""Postings follow the document's own date, not the day it was saved: a September invoice
typed in October belongs to September's ledger, account totals, VAT return and dashboard.
Re-dating a document moves its posting (reversal on the old date, fresh journal on the new
one); a locked period refuses postings added to it or moved out of it."""
from datetime import date, timedelta
from decimal import Decimal

from app.models import Account, AccountPeriodTotal, JournalEntry, SourceTransaction, TaxLine
from tests.test_registration import _register


def _save(client, headers, collection, record, status=200):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": collection, "record": record})
    assert r.status_code == status, r.text
    return r


def _invoice(no, day, amount=100):
    return {"invoice_no": no, "customer": "Dated Cust", "date": day, "status": "Issued",
            "lines": [{"description": "Item", "qty": 1, "unit_price": amount, "vat_rate": 5}]}


def _company(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _tx(db, cid, ref):
    db.expire_all()
    return db.query(SourceTransaction).filter(SourceTransaction.company_id == cid, SourceTransaction.reference == ref).one()


def _live_journal_dates(db, cid, tx):
    journals = db.query(JournalEntry).filter(JournalEntry.company_id == cid, JournalEntry.source_id == tx.id).all()
    reversed_ids = {r.source_id for r in db.query(JournalEntry).filter(
        JournalEntry.source_module == "reversal", JournalEntry.source_id.in_([j.id for j in journals]))}
    return [j.entry_date.date() for j in journals if j.id not in reversed_ids]


def _month_net(db, cid, code, period):
    """Credit minus debit on an account in one month, from the stored per-month totals."""
    account = db.query(Account).filter(Account.company_id == cid, Account.code == code).one()
    rows = db.query(AccountPeriodTotal).filter(AccountPeriodTotal.company_id == cid, AccountPeriodTotal.account_id == account.id,
                                               AccountPeriodTotal.period == period).all()
    return sum((r.credit - r.debit for r in rows), Decimal("0"))


def _vat(client, headers, period):
    r = client.get("/api/v1/tax/vat-return", headers=headers, params={"period": period})
    assert r.status_code == 200, r.text
    return Decimal(r.json()["output_vat"]), Decimal(r.json()["input_vat"])


def test_backdated_invoice_posts_in_its_own_month(client, db):
    h = _register(client, "docdate-back")
    cid = _company(client, h)
    _save(client, h, "salesInvoices", _invoice("DD-1", "2025-09-14"))
    tx = _tx(db, cid, "DD-1")
    assert tx.document_date == date(2025, 9, 14)
    assert _live_journal_dates(db, cid, tx) == [date(2025, 9, 14)]
    assert [t.period for t in db.query(TaxLine).filter(TaxLine.source_id == tx.id)] == ["2025-09"]
    assert _month_net(db, cid, "3000", "2025-09") == Decimal("100.00")
    assert _vat(client, h, "2025-09") == (Decimal("5.00"), Decimal("0.00"))
    this_month = date.today().strftime("%Y-%m")
    assert _month_net(db, cid, "3000", this_month) == 0

    # purchases, bills and payments follow their date too
    _save(client, h, "purchaseRecords", {"ref": "DD-P1", "supplier": "S", "date": "2025-08-03", "subtotal": 40,
                                         "vat_amount": 2, "total": 42})
    _save(client, h, "bills", {"bill_no": "DD-B1", "ref": "DD-B1", "vendor": "V", "date": "03/07/2025", "subtotal": 10, "vat": 0.5, "total": 10.5})
    _save(client, h, "payments", {"ref": "DD-R1", "type": "Customer Receipt", "contact": "Dated Cust", "amount": 105,
                                  "date": "2025-10-02"})
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-P1")) == [date(2025, 8, 3)]
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-B1")) == [date(2025, 7, 3)]   # day-first
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-R1")) == [date(2025, 10, 2)]
    assert _vat(client, h, "2025-08") == (Decimal("0.00"), Decimal("2.00"))
    assert _vat(client, h, "2025-07") == (Decimal("0.00"), Decimal("0.50"))


def test_invoice_without_a_date_posts_today(client, db):
    h = _register(client, "docdate-none")
    cid = _company(client, h)
    rec = _invoice("DD-N1", "")
    _save(client, h, "salesInvoices", rec)
    tx = _tx(db, cid, "DD-N1")
    assert tx.document_date == date.today() or tx.document_date == (date.today() - timedelta(days=1))
    assert _month_net(db, cid, "3000", tx.document_date.strftime("%Y-%m")) == Decimal("100.00")


def test_changing_the_date_moves_the_posting(client, db):
    h = _register(client, "docdate-move")
    cid = _company(client, h)
    _save(client, h, "salesInvoices", _invoice("DD-M1", "2025-09-14"))
    _save(client, h, "salesInvoices", _invoice("DD-M1", "2025-11-02"))
    tx = _tx(db, cid, "DD-M1")
    assert _live_journal_dates(db, cid, tx) == [date(2025, 11, 2)]
    assert _month_net(db, cid, "3000", "2025-09") == 0          # original and its reversal
    assert _month_net(db, cid, "3000", "2025-11") == Decimal("100.00")
    assert _vat(client, h, "2025-09") == (Decimal("0.00"), Decimal("0.00"))
    assert _vat(client, h, "2025-11") == (Decimal("5.00"), Decimal("0.00"))
    # the month the edit happened in is untouched
    assert _month_net(db, cid, "3000", date.today().strftime("%Y-%m")) == 0

    # an unchanged re-save adds nothing
    count = db.query(JournalEntry).filter(JournalEntry.company_id == cid).count()
    _save(client, h, "salesInvoices", _invoice("DD-M1", "2025-11-02"))
    db.expire_all()
    assert db.query(JournalEntry).filter(JournalEntry.company_id == cid).count() == count

    # an amount edit on a back-dated invoice corrects its own month, not the month of the edit
    _save(client, h, "salesInvoices", _invoice("DD-M1", "2025-11-02", amount=300))
    assert _month_net(db, cid, "3000", "2025-11") == Decimal("300.00")
    assert _month_net(db, cid, "3000", date.today().strftime("%Y-%m")) == 0
    assert _vat(client, h, "2025-11") == (Decimal("15.00"), Decimal("0.00"))

    # a payment moves too
    _save(client, h, "payments", {"ref": "DD-MP", "type": "Supplier Payment", "contact": "S", "amount": 50, "date": "2025-09-30"})
    _save(client, h, "payments", {"ref": "DD-MP", "type": "Supplier Payment", "contact": "S", "amount": 50, "date": "2025-10-01"})
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-MP")) == [date(2025, 10, 1)]
    assert _month_net(db, cid, "1000", "2025-09") == 0
    assert _month_net(db, cid, "1000", "2025-10") == Decimal("50.00")


def test_dashboard_months_follow_the_document_date(client, db):
    from app.routers.reports import monthly_revenue_vat

    h = _register(client, "docdate-dash")
    cid = _company(client, h)
    last_month = date.today().replace(day=1) - timedelta(days=1)
    _save(client, h, "salesInvoices", _invoice("DD-D1", last_month.isoformat()))
    _save(client, h, "purchaseRecords", {"ref": "DD-D2", "supplier": "S", "date": last_month.isoformat(),
                                         "subtotal": 40, "vat_amount": 2, "total": 42})
    rows = {r["period"]: r for r in monthly_revenue_vat(db, cid, [])}
    assert Decimal(rows[last_month.strftime("%Y-%m")]["sales"]) == Decimal("105.00")
    assert Decimal(rows[last_month.strftime("%Y-%m")]["purchases"]) == Decimal("42.00")
    assert date.today().strftime("%Y-%m") not in rows


def test_locked_period_refuses_postings_into_or_out_of_it(client, db):
    h = _register(client, "docdate-lock")
    cid = _company(client, h)
    _save(client, h, "salesInvoices", _invoice("DD-L1", "2025-06-10"))
    _save(client, h, "payments", {"ref": "DD-LP", "type": "Customer Receipt", "contact": "C", "amount": 20, "date": "2025-06-11"})
    _save(client, h, "lockedPeriods", {"id": "2025-06", "locked": True})

    # into the locked month: a new invoice, and a receipt (no collection-level lock for payments)
    _save(client, h, "salesInvoices", _invoice("DD-L2", "2025-06-20"), status=423)
    _save(client, h, "payments", {"ref": "DD-LP2", "type": "Customer Receipt", "contact": "C", "amount": 5,
                                  "date": "2025-06-15"}, status=423)
    # out of it: re-dating a June document to July must not silently move it
    _save(client, h, "salesInvoices", _invoice("DD-L1", "2025-07-01"), status=423)
    _save(client, h, "payments", {"ref": "DD-LP", "type": "Customer Receipt", "contact": "C", "amount": 20,
                                  "date": "2025-07-01"}, status=423)
    tx = _tx(db, cid, "DD-L1")
    assert _live_journal_dates(db, cid, tx) == [date(2025, 6, 10)]
    assert _month_net(db, cid, "3000", "2025-06") == Decimal("100.00")
    assert _month_net(db, cid, "3000", "2025-07") == 0
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-LP")) == [date(2025, 6, 11)]

    # unlocked again, the move goes through
    _save(client, h, "lockedPeriods", {"id": "2025-06", "locked": False})
    _save(client, h, "salesInvoices", _invoice("DD-L1", "2025-07-01"))
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-L1")) == [date(2025, 7, 1)]


def test_posting_from_before_document_dates_moves_only_across_months(client, db):
    """Rows posted before this change carry no document_date and a journal dated the day
    they were saved. Re-saving one in the same month keeps its journal where it is (no
    reversal churn); one whose date is in another month moves to it."""
    h = _register(client, "docdate-legacy")
    cid = _company(client, h)
    for ref, saved_on in (("DD-OLD1", "2025-05-20"), ("DD-OLD2", "2025-05-20")):
        _save(client, h, "salesInvoices", _invoice(ref, saved_on))
        tx = _tx(db, cid, ref)
        tx.document_date = None
        db.commit()
    count = db.query(JournalEntry).filter(JournalEntry.company_id == cid).count()

    _save(client, h, "salesInvoices", _invoice("DD-OLD1", "2025-05-03"))   # same month: stays
    tx = _tx(db, cid, "DD-OLD1")
    assert tx.document_date == date(2025, 5, 3)
    assert _live_journal_dates(db, cid, tx) == [date(2025, 5, 20)]
    assert db.query(JournalEntry).filter(JournalEntry.company_id == cid).count() == count
    _save(client, h, "salesInvoices", _invoice("DD-OLD1", "2025-05-03"))   # and stays stable
    assert db.query(JournalEntry).filter(JournalEntry.company_id == cid).count() == count

    _save(client, h, "salesInvoices", _invoice("DD-OLD2", "2025-04-28"))   # another month: moves
    assert _live_journal_dates(db, cid, _tx(db, cid, "DD-OLD2")) == [date(2025, 4, 28)]
    assert _month_net(db, cid, "3000", "2025-05") == Decimal("100.00")
    assert _month_net(db, cid, "3000", "2025-04") == Decimal("100.00")
