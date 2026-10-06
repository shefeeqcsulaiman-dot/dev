"""Stock accounting: perpetual companies post stock purchases to 1200 Inventory and each sale's cost
(Dr 5000 COGS / Cr 1200) at average purchase cost; a purchase line categorised to a ledger posts to
that ledger; periodic (existing) companies keep posting purchases to 4000 with no COGS."""
from decimal import Decimal
from uuid import uuid4

from app.models import Account, Company, JournalEntry, JournalLine
from tests.test_company_stock_mode import _create_company
from tests.test_module_permissions import _make_superadmin


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"}, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _balances(db, company_id):
    """Net debit (debit - credit) per account code across every posted journal, reversals included."""
    db.expire_all()
    rows = (db.query(Account.code, JournalLine.debit, JournalLine.credit)
            .join(JournalLine, JournalLine.account_id == Account.id)
            .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
            .filter(JournalEntry.company_id == company_id).all())
    out: dict[str, Decimal] = {}
    for code, debit, credit in rows:
        out[code] = out.get(code, Decimal("0")) + Decimal(debit) - Decimal(credit)
    return {k: v for k, v in out.items() if v}


def _company(client, db, **extra):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"inv-{tag}")
    company_id, headers = _create_company(client, sa, tag, **extra)
    return tag, company_id, headers, sa


def _buy(client, headers, ref, qty, unit_cost, category="", product="Widget", sku="WID-1"):
    sub = qty * unit_cost
    _save(client, headers, "purchaseRecords", {"ref": ref, "supplier": "Supp", "date": "2026-10-01",
        "subtotal": sub, "vat_amount": round(sub * 0.05, 2), "total": round(sub * 1.05, 2),
        "lines": [{"sku": sku, "product": product, "category": category, "quantity": qty, "unit_cost": unit_cost, "line_total": sub}]})


def _sell(client, headers, no, qty, price=100, status="Issued", sku="WID-1"):
    _save(client, headers, "salesInvoices", {"invoice_no": no, "customer": "Cust", "date": "2026-10-02", "status": status,
        "lines": [{"description": "Widget", "product_code": sku, "qty": qty, "unit_price": price, "vat_rate": 5}]})


def test_new_companies_default_to_perpetual_existing_ones_stay_periodic(client, db):
    _, company_id, _, _ = _company(client, db)
    assert db.get(Company, company_id).inventory_accounting == "perpetual"


def test_perpetual_purchase_goes_to_inventory_and_sales_post_cogs(client, db):
    tag, cid, h, _ = _company(client, db)
    _buy(client, h, f"P1-{tag}", 10, 40)          # 10 @ 40
    _buy(client, h, f"P2-{tag}", 10, 60)          # 10 @ 60 -> average 50
    b = _balances(db, cid)
    assert b.get("1200") == Decimal("1000.00") and "4000" not in b, b

    _sell(client, h, f"S1-{tag}", 3)              # cost 3 x 50 = 150
    b = _balances(db, cid)
    assert b.get("5000") == Decimal("150.00") and b.get("1200") == Decimal("850.00"), b

    _sell(client, h, f"S1-{tag}", 5)              # edited to 5 -> cost 250 (reversal + fresh entry)
    b = _balances(db, cid)
    assert b.get("5000") == Decimal("250.00") and b.get("1200") == Decimal("750.00"), b

    _sell(client, h, f"S1-{tag}", 5, status="Cancelled")   # cancelled -> cost fully reversed
    b = _balances(db, cid)
    assert "5000" not in b and b.get("1200") == Decimal("1000.00"), b


def test_purchase_line_category_picks_the_ledger(client, db):
    tag, cid, h, _ = _company(client, db)
    _buy(client, h, f"P-RENT-{tag}", 1, 500, category="Rent Expense", product="Office rent", sku="RENT")
    b = _balances(db, cid)
    assert b.get("6100") == Decimal("500.00") and "1200" not in b and "4000" not in b, b


def test_pos_sale_and_refund_post_and_return_cost(client, db):
    tag, cid, h, _ = _company(client, db)
    _buy(client, h, f"P-{tag}", 4, 25)
    _save(client, h, "posSales", {"id": f"R-{tag}", "receipt_no": f"R-{tag}", "status": "completed", "subtotal": 200, "vat": 10,
        "total": 210, "payment_method": "cash", "items": [{"code": "WID-1", "name": "Widget", "qty": 2, "price": 100}]})
    assert _balances(db, cid).get("5000") == Decimal("50.00")
    _save(client, h, "posSales", {"id": f"RET-{tag}", "receipt_no": f"RET-{tag}", "type": "refund", "status": "completed",
        "subtotal": -100, "vat": -5, "total": -105, "payment_method": "cash", "items": [{"code": "WID-1", "name": "Widget", "qty": 1, "price": 100}]})
    assert _balances(db, cid).get("5000") == Decimal("25.00")


def test_deleting_a_sale_reverses_nothing_left_behind(client, db):
    tag, cid, h, _ = _company(client, db)
    _buy(client, h, f"P-{tag}", 2, 30)
    _sell(client, h, f"S-{tag}", 1)
    assert _balances(db, cid).get("5000") == Decimal("30.00")
    r = client.post("/api/v1/app-data?action=delete", headers=h, json={"collection": "salesInvoices", "record": {"invoice_no": f"S-{tag}"}})
    assert r.status_code == 200, r.text
    assert "5000" not in _balances(db, cid)


def test_periodic_company_keeps_purchases_on_4000_without_cogs(client, db):
    tag, cid, h, _ = _company(client, db, inventory_accounting="periodic")
    _buy(client, h, f"P-{tag}", 2, 30)
    _sell(client, h, f"S-{tag}", 1)
    b = _balances(db, cid)
    assert b.get("4000") == Decimal("60.00") and "1200" not in b and "5000" not in b, b


def test_without_stock_company_has_no_inventory_postings(client, db):
    tag, cid, h, _ = _company(client, db, stock_mode="without_stock")
    _buy(client, h, f"P-{tag}", 2, 30)
    _sell(client, h, f"S-{tag}", 1)
    b = _balances(db, cid)
    assert b.get("4000") == Decimal("60.00") and "1200" not in b and "5000" not in b, b


def _pl(client, h):
    r = client.get("/api/v1/reports/summary", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["profit_loss"]


def test_perpetual_profit_and_loss_uses_cost_of_goods_sold(client, db):
    tag, cid, h, _ = _company(client, db)
    _buy(client, h, f"PL1-{tag}", 10, 50)        # 500 into stock
    _sell(client, h, f"PLS-{tag}", 4, price=100)  # 400 revenue, cost 4 x 50 = 200
    pl = _pl(client, h)
    assert Decimal(pl["cogs"]) == Decimal("200.00"), pl
    assert Decimal(pl["gross_profit"]) == Decimal("200.00"), pl
    dash = client.get("/api/v1/reports/dashboard", headers=h).json()
    assert Decimal(dash["purchase_summary"]["cost_of_sales"]) == Decimal("200.00")


def test_periodic_profit_and_loss_still_expenses_purchases(client, db):
    tag, cid, h, _ = _company(client, db, inventory_accounting="periodic")
    _buy(client, h, f"PP1-{tag}", 10, 50)
    _sell(client, h, f"PPS-{tag}", 4, price=100)
    assert Decimal(_pl(client, h)["cogs"]) == Decimal("500.00")
