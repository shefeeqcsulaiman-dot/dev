"""Report figures stamped on each record (doc_index.doc_figures(), fig_* columns): the
purchase summary, branch performance, unposted-purchase list and expense total are SQL
sums of them, with the rules reports.py used on decoded payloads."""
import json
from uuid import uuid4

from app.doc_index import doc_figures
from app.models import AppDataRecord, Branch, Company, SourceTransaction, TaxLine


def _company(db):
    tag = uuid4().hex[:8]
    company = Company(name=f"Fig Co {tag}", trn=f"FIG-{tag}")
    db.add(company)
    db.flush()
    return company


def _add(db, company, collection, payload, branch=None, key=None):
    rec = AppDataRecord(company_id=company.id, collection=collection, branch_id=branch.id if branch else None,
                        record_key=key or payload.get("id") or uuid4().hex, payload=json.dumps(payload))
    db.add(rec)
    db.flush()
    return rec


def test_doc_figures_follow_the_report_rules():
    f = doc_figures({"total": 0, "net_amount": "200", "vat_amount": "10", "status": " Pending "}, "purchaseRecords")
    assert (f["fig_gross"], f["fig_net"], f["fig_vat"], f["fig_status"]) == (210, 200, 10, "pending")
    f = doc_figures({"lines": [{"total": "50"}, {"line_total": "25"}], "paid": "30"}, "bills")
    assert (f["fig_gross"], f["fig_net"], f["fig_paid"], f["fig_status"]) == (75, 75, 30, "")
    f = doc_figures({"id": "x1"}, "purchaseRecords")
    assert f["fig_ref"] == "purchase-x1"
    f = doc_figures({"bill_no": " BL-9 "}, "bills")
    assert f["fig_ref"] == "bl-9"
    f = doc_figures({"invoice_no": "INV-1", "subtotal": "100", "total": "105", "vat_amount": "5"}, "salesInvoices")
    assert (f["fig_ref"], f["fig_net"], f["fig_gross"], f["fig_vat"]) == ("inv-1", 100, 105, 5)
    f = doc_figures({"total": "12", "subtotal": "10"}, "expenses")
    assert f["fig_net"] == 10
    # An amount money() can't read no longer fails the whole report: it adds nothing.
    assert doc_figures({"total": "AED 9"}, "purchaseRecords")["fig_gross"] is None


def test_purchase_summary_and_branch_performance(db):
    from app.routers import reports

    company = _company(db)
    x = Branch(company_id=company.id, name="Branch X")
    y = Branch(company_id=company.id, name="Branch Y")
    db.add_all([x, y])
    db.flush()
    _add(db, company, "purchaseRecords", {"id": "p1", "ref": "PR-1", "total": "105.00", "net_amount": "100", "tax_amount": "5", "status": "Paid"})
    _add(db, company, "purchaseRecords", {"id": "p2", "total": 0, "net_amount": "200", "vat_amount": "10", "status": " pending "}, branch=x)
    _add(db, company, "purchaseRecords", {"id": "p3", "lines": [{"total": "50"}, {"line_total": "25"}], "status": "SETTLED", "paid": "30"})
    _add(db, company, "bills", {"id": "b1", "bill_no": "BL-1", "grand_total": "52.50", "subtotal": "50", "status": "Awaiting Payment", "paid_amount": "20"})
    _add(db, company, "salesInvoices", {"invoice_no": "S1", "status": "Paid", "subtotal": "100", "total": "105"}, branch=x)
    _add(db, company, "salesInvoices", {"invoice_no": "S2", "status": "draft", "subtotal": "40", "total": "42"})
    _add(db, company, "salesInvoices", {"invoice_no": "S3", "status": "sent", "net_amount": "10", "amount": "11"})
    _add(db, company, "expenses", {"id": "e1", "total": "12", "subtotal": "10"})
    _add(db, company, "expenses", {"id": "e2", "amount": "7"})
    db.commit()

    s = reports._purchase_summary(db, company.id)
    assert s == {"total": "442.50", "net": "425.00", "paid": "155.00", "paid_count": 2, "pending_count": 2,
                 "total_count": 4, "payment_rate": 35}
    # Branch Y sees its own rows plus rows with no branch, not Branch X's.
    sy = reports._purchase_summary(db, company.id, y.id)
    assert (sy["total"], sy["net"], sy["total_count"]) == ("232.50", "225.00", 3)

    perf = reports._build_branch_performance(db, company.id)
    by_name = {r["name"]: r for r in perf["branches"]}
    assert by_name["Branch X"]["revenue"] == "100.00" and by_name["Branch X"]["purchases"] == "200.00"
    assert by_name["Branch X"]["invoices_collected"] == {"count": 1, "amount": "105.00"}
    assert by_name["Branch Y"]["revenue"] == "0.00"
    un = perf["unassigned"]
    assert un["revenue"] == "10.00" and un["purchases"] == "225.00"
    assert un["invoices_pending"] == {"count": 1, "amount": "11.00"}

    from sqlalchemy import func
    expense_total = db.query(func.sum(AppDataRecord.fig_net)).filter(
        AppDataRecord.company_id == company.id, AppDataRecord.collection == "expenses").scalar()
    assert expense_total == 17


def test_unposted_purchases_skip_posted_references(db):
    from app.routers import reports

    company = _company(db)
    _add(db, company, "purchaseRecords", {"id": "p1", "ref": "PR-1", "total": "105"})
    _add(db, company, "purchaseRecords", {"id": "p2", "total": "50"})             # posts as PURCHASE-p2
    _add(db, company, "purchaseRecords", {"id": "p3", "ref": "PR-3", "total": "70"})
    _add(db, company, "bills", {"id": "b1", "bill_no": "BL-1", "total": "40"})
    _add(db, company, "bills", {"id": "b2", "total": "30"})                      # posts as BILL-b2
    for ref, module in (("PR-1", "purchase"), ("PURCHASE-p2", "purchase"), (" bl-1 ", "purchase_bill"), ("BILL-b2", "purchase_bill")):
        st = SourceTransaction(company_id=company.id, module=module, reference=ref)
        db.add(st)
        db.flush()
        db.add(TaxLine(company_id=company.id, source_id=st.id, direction="input", tax_amount=1))
    # A posted sale doesn't count as a posted purchase.
    st = SourceTransaction(company_id=company.id, module="sales", reference="PR-3")
    db.add(st)
    db.flush()
    db.add(TaxLine(company_id=company.id, source_id=st.id, direction="output", tax_amount=1))
    db.commit()

    rows = reports.app_purchase_records(db, company.id)
    assert [r["id"] for r in rows] == ["p3"]


def test_figures_follow_edits(db):
    from app.routers import reports

    company = _company(db)
    rec = _add(db, company, "bills", {"id": "b1", "total": "100", "status": "pending"})
    db.commit()
    assert reports._purchase_summary(db, company.id)["paid"] == "0.00"
    rec.payload = json.dumps({"id": "b1", "total": "100", "status": "Paid"})
    db.commit()
    assert reports._purchase_summary(db, company.id)["paid"] == "100.00"
    db.delete(rec)
    db.commit()
    assert reports._purchase_summary(db, company.id)["total_count"] == 0
