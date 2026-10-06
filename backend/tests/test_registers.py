"""Paged registers for bills, payments and quotations (/app-data/registers/...)."""
import datetime as dt
from uuid import uuid4

import pytest

from tests.conftest import ensure_user


@pytest.fixture()
def tenant(client, db):
    """A fresh company per test, so counts and totals start from zero."""
    tag = uuid4().hex[:8]
    email = f"rg-{tag}@taxflowqa.com"
    ensure_user(db, email, f"92{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _get(client, headers, path, **params):
    r = client.get(f"/api/v1/app-data{path}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _bill(no, vendor="Gulf Supplies", total=105, status="Awaiting Payment", **extra):
    return {"id": f"BILL-{no}", "bill_no": no, "vendor": vendor, "date": "2026-09-10", "due": "2026-10-10",
            "subtotal": total / 1.05, "vat": total - total / 1.05, "total": total, "status": status, **extra}


def _supplier_payment(ref, amount, doc):
    return {"type": "Supplier Payment", "ref": ref, "contact": "Gulf Supplies", "amount": amount, "date": "2026-09-20",
            "document_ref": doc, "bill_no": doc, "allocations": [{"doc_ref": doc, "amount": amount}]}


# ── bills ──────────────────────────────────────────────────────────────────────

def test_bills_page_and_search(client, tenant):
    for i in range(5):
        _save(client, tenant, "bills", _bill(f"B-{i}", vendor="Alpha" if i % 2 else "Beta"))
    page = _get(client, tenant, "/registers/bills", limit=2)
    assert page["total"] == 5 and [r["bill_no"] for r in page["records"]] == ["B-4", "B-3"]
    assert _get(client, tenant, "/registers/bills", q="alpha")["total"] == 2
    assert _get(client, tenant, "/registers/bills/parties")["parties"] == ["Alpha", "Beta"]


def test_supplier_payments_settle_bills_and_feed_cards(client, tenant):
    today = dt.date.today()
    _save(client, tenant, "bills", _bill("B-DUE", total=100, due=(today + dt.timedelta(days=3)).isoformat()))
    _save(client, tenant, "bills", _bill("B-LATER", total=200, due=(today + dt.timedelta(days=30)).isoformat()))
    _save(client, tenant, "bills", _bill("B-DONE", total=50, status="Paid"))
    s = _get(client, tenant, "/registers/bills/summary")
    assert (s["count"], s["open_total"], s["open_count"], s["due_week_total"], s["due_week_count"]) == (3, 300, 2, 100, 1)

    _save(client, tenant, "payments", _supplier_payment("PAY-1", 60, "B-DUE"))
    bill = _get(client, tenant, "/registers/bills/by-key", key="b-due")["record"]
    assert (bill["status"], bill["amount_paid"], bill["balance_due"]) == ("Partial", 60, 40)
    s = _get(client, tenant, "/registers/bills/summary")
    assert (s["open_total"], s["due_week_total"]) == (240, 40)
    assert _get(client, tenant, "/registers/bills/vendor-balances")["balances"] == {"gulf supplies": 240}

    _save(client, tenant, "payments", _supplier_payment("PAY-2", 40, "B-DUE"))
    assert _get(client, tenant, "/registers/bills/by-key", key="B-DUE")["record"]["status"] == "Paid"
    assert _get(client, tenant, "/registers/bills/summary")["open_count"] == 1


def test_customer_receipts_do_not_settle_bills(client, tenant):
    _save(client, tenant, "bills", _bill("SAME-1", total=100))
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-1", "amount": 100, "invoice_no": "SAME-1"})
    assert _get(client, tenant, "/registers/bills/by-key", key="SAME-1")["record"]["status"] == "Awaiting Payment"


def test_open_payables_for_suppliers_include_purchases(client, tenant):
    _save(client, tenant, "bills", _bill("B-OPEN", total=100) | {"date": "2026-09-02"})
    _save(client, tenant, "purchaseRecords", {"ref": "PUR-1", "supplier": "Gulf Supplies", "date": "2026-09-01",
                                              "total": 300, "status": "Pending"})
    _save(client, tenant, "purchaseRecords", {"ref": "PUR-PAID", "supplier": "Gulf Supplies", "date": "2026-09-01",
                                              "total": 80, "status": "Paid"})
    _save(client, tenant, "payments", _supplier_payment("PAY-P", 100, "PUR-1"))
    docs = _get(client, tenant, "/payables/open", side="supplier", party="gulf supplies")["documents"]
    assert [(d["ref"], d["amount"], d["source"]) for d in docs] == [("PUR-1", 200, "Purchase Invoice"), ("B-OPEN", 100, "Bill")]
    assert _get(client, tenant, "/payables/open", side="customer")["documents"] == []


def test_bulk_deleting_supplier_payment_reopens_bill(client, tenant):
    _save(client, tenant, "bills", _bill("B-BD", total=100))
    _save(client, tenant, "payments", _supplier_payment("PAY-BD", 100, "B-BD"))
    r = client.post("/api/v1/app-data?action=bulk-delete", headers=tenant, json={"collection": "payments", "records": [{"ref": "PAY-BD"}]})
    assert r.status_code == 200, r.text
    assert _get(client, tenant, "/registers/bills/by-key", key="B-BD")["record"]["status"] == "Awaiting Payment"


# ── payments ───────────────────────────────────────────────────────────────────

def _receipt(ref, amount, date, contact="Acme"):
    return {"type": "Customer Receipt", "ref": ref, "contact": contact, "amount": amount, "date": date, "invoice_no": "-"}


def test_payments_by_kind_summary_and_next_ref(client, tenant):
    _save(client, tenant, "payments", _receipt("RCT-2026-0007", 100, "2026-09-01"))
    _save(client, tenant, "payments", _receipt("RCT-2026-0012", 50, "2026-09-03"))
    _save(client, tenant, "payments", _supplier_payment("PAY-2026-0003", 30, "-"))
    assert _get(client, tenant, "/registers/payments", kind="customer")["total"] == 2
    assert [r["ref"] for r in _get(client, tenant, "/registers/payments", kind="supplier")["records"]] == ["PAY-2026-0003"]
    s = _get(client, tenant, "/registers/payments/summary")
    assert (s["count"], s["count_in"], s["count_out"], s["inflow"], s["outflow"]) == (3, 2, 1, 150, 30)
    year = dt.date.today().year
    assert _get(client, tenant, "/registers/payments/next-ref", kind="customer")["ref"] == f"RCT-{year}-0013"
    assert _get(client, tenant, "/registers/payments/next-ref", kind="supplier")["ref"] == f"PAY-{year}-0004"


def test_payments_running_balance_across_pages(client, tenant):
    _save(client, tenant, "payments", _receipt("R-1", 100, "2026-09-01"))
    _save(client, tenant, "payments", _receipt("R-2", 40, "2026-09-05"))
    _save(client, tenant, "payments", _supplier_payment("P-1", 25, "-") | {"date": "2026-09-03"})
    first = _get(client, tenant, "/registers/payments", sort="date-desc", running=True, limit=2)
    assert [r["ref"] for r in first["records"]] == ["R-2", "P-1"] and first["running_before"] == 0
    second = _get(client, tenant, "/registers/payments", sort="date-desc", running=True, limit=2, offset=2)
    assert [r["ref"] for r in second["records"]] == ["R-1"] and second["running_before"] == 15  # +40 -25


def test_payments_sort_by_amount(client, tenant):
    for ref, amount in (("A", 10), ("B", 30), ("C", 20)):
        _save(client, tenant, "payments", _supplier_payment(ref, amount, "-"))
    assert [r["ref"] for r in _get(client, tenant, "/registers/payments", kind="supplier", sort="amount-desc")["records"]] == ["B", "C", "A"]


# ── quotations ─────────────────────────────────────────────────────────────────

def test_quotations_page_search_and_summary(client, tenant):
    for i, customer in enumerate(("Blue LLC", "Red LLC", "Blue LLC")):
        _save(client, tenant, "quotations", {"quote_no": f"QT-{i}", "customer": customer, "date": "2026-09-0{}".format(i + 1),
                                             "total": 100 * (i + 1), "status": "Sent", "owner": "Sales Team"})
    page = _get(client, tenant, "/registers/quotations", limit=2)
    assert page["total"] == 3 and [r["quote_no"] for r in page["records"]] == ["QT-2", "QT-1"]
    assert _get(client, tenant, "/registers/quotations", q="blue")["total"] == 2
    assert _get(client, tenant, "/registers/quotations/summary") == {"ok": True, "count": 3, "total": 600}


def test_unknown_register_and_tenant_isolation(client, tenant, second_tenant_headers):
    r = client.get("/api/v1/app-data/registers/employees", headers=tenant)
    assert r.status_code == 404
    _save(client, tenant, "bills", _bill("MINE-B"))
    assert _get(client, second_tenant_headers, "/registers/bills", q="MINE-B")["total"] == 0


def test_bootstrap_no_longer_ships_paged_registers(client, tenant):
    _save(client, tenant, "bills", _bill("BOOT-B"))
    _save(client, tenant, "payments", _receipt("BOOT-R", 10, "2026-09-01"))
    _save(client, tenant, "quotations", {"quote_no": "BOOT-Q", "customer": "Boot", "total": 5})
    data = client.get("/api/v1/app-data", headers=tenant).json()["data"]
    for collection in ("bills", "payments", "quotations"):
        assert collection not in data


# ── purchase documents ─────────────────────────────────────────────────────────

def test_purchase_documents_listed_without_file_and_fetched_with_it(client, tenant, second_tenant_headers):
    for i in range(3):
        _save(client, tenant, "purchaseDocuments", {"id": f"DOC-{i}", "name": f"inv{i}.pdf", "base64": "data:application/pdf;base64,QUFB",
                                                   "status": "Extracted", "invoices": [{"invoice_no": f"P-{i}"}]})
    page = _get(client, tenant, "/purchase-documents", limit=2)
    assert page["total"] == 3 and page["has_more"] is True
    assert all("base64" not in r and r["invoices"] for r in page["records"])
    full = _get(client, tenant, "/purchase-documents/DOC-1")["record"]
    assert full["base64"].endswith("QUFB")
    assert client.get("/api/v1/app-data/purchase-documents/DOC-1", headers=second_tenant_headers).status_code == 404
    assert "purchaseDocuments" not in client.get("/api/v1/app-data", headers=tenant).json()["data"]


# ── expenses ───────────────────────────────────────────────────────────────────

def test_expenses_page_status_filter_and_summary(client, tenant):
    rows = [("E-1", "Direct Expense", 100, "Pending"), ("E-2", "Indirect Expense", 50, "Approved"),
            ("E-3", "Supplies", 30, "Rejected"), ("E-4", "Direct Expense", 20, "Approved")]
    for ref, category, total, status in rows:
        _save(client, tenant, "expenses", {"ref": ref, "date": "2026-09-01", "category": category, "description": ref,
                                           "amount": total, "vat_amount": 0, "total": total, "status": status})
    assert _get(client, tenant, "/registers/expenses", limit=2)["total"] == 4
    assert [r["ref"] for r in _get(client, tenant, "/registers/expenses", status="pending")["records"]] == ["E-1"]
    s = _get(client, tenant, "/registers/expenses/summary")
    assert (s["total"], s["pending"], s["approved"], s["rejected"], s["direct"]) == (200, 100, 70, 30, 120)
    data = client.get("/api/v1/app-data", headers=tenant).json()["data"]
    assert "expenses" not in data and "ledger" not in data
