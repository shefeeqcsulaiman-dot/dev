"""Paged sales register: /app-data/sales-invoices and the summary columns behind it."""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import AppDataRecord
from tests.conftest import ensure_user


@pytest.fixture()
def tenant(client, db):
    """A fresh company per test, so counts and totals start from zero."""
    tag = uuid4().hex[:8]
    email = f"sr-{tag}@taxflowqa.com"
    ensure_user(db, email, f"91{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def _invoice(no, customer="Acme LLC", total=105, status="Issued", **extra):
    return {
        "invoice_no": no, "customer": customer, "date": "2026-09-15", "status": status,
        "subtotal": total / 1.05, "vat_amount": total - total / 1.05, "total": total,
        "lines": [{"description": "Widget", "qty": 2, "unit_price": total / 2.1, "vat_rate": 5}],
        **extra,
    }


def _get(client, headers, path, **params):
    r = client.get(f"/api/v1/app-data/sales-invoices{path}", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_pages_newest_first_and_counts_total(client, tenant):
    for i in range(7):
        _save(client, tenant, "salesInvoices", _invoice(f"INV-{i:03d}"))
    first = _get(client, tenant, "", limit=3)
    assert first["total"] == 7 and first["has_more"] is True
    assert [r["invoice_no"] for r in first["records"]] == ["INV-006", "INV-005", "INV-004"]
    last = _get(client, tenant, "", limit=3, offset=6)
    assert [r["invoice_no"] for r in last["records"]] == ["INV-000"] and last["has_more"] is False


def test_returns_are_listed_separately(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("INV-1"))
    _save(client, tenant, "salesInvoices", _invoice("SR-1", document_type="Sales Return"))
    assert [r["invoice_no"] for r in _get(client, tenant, "")["records"]] == ["INV-1"]
    assert [r["invoice_no"] for r in _get(client, tenant, "", kind="return")["records"]] == ["SR-1"]
    assert _get(client, tenant, "", kind="all")["total"] == 2


def test_search_by_number_customer_and_line_text(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("INV-A1", customer="Blue Sky Trading"))
    _save(client, tenant, "salesInvoices", _invoice("INV-B2", customer="Red Rock LLC"))
    assert [r["invoice_no"] for r in _get(client, tenant, "", q="blue sky")["records"]] == ["INV-A1"]
    assert [r["invoice_no"] for r in _get(client, tenant, "", q="b2")["records"]] == ["INV-B2"]
    assert _get(client, tenant, "", contains="widget")["total"] == 2
    assert _get(client, tenant, "", product="WIDGET", kind="all")["total"] == 2
    assert _get(client, tenant, "", product="widg", kind="all")["total"] == 0  # whole line name only
    assert _get(client, tenant, "", customer="RED ROCK LLC")["total"] == 1
    # LIKE wildcards in the search text are literal
    assert _get(client, tenant, "", q="%")["total"] == 0


def test_summary_buckets_and_receipts(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("INV-P", total=100, status="Paid"))
    _save(client, tenant, "salesInvoices", _invoice("INV-O", total=200, status="Overdue"))
    _save(client, tenant, "salesInvoices", _invoice("INV-X", total=300))
    _save(client, tenant, "salesInvoices", _invoice("SR-1", total=50, document_type="Sales Return"))
    s = _get(client, tenant, "/summary")
    assert (s["count"], s["total"], s["collected"], s["overdue"], s["pending"]) == (3, 600, 100, 200, 300)

    # A receipt for part of INV-X makes it Partial (still pending); paying the rest makes it Paid.
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-1", "contact": "Acme LLC", "amount": 120,
                                       "allocations": [{"doc_ref": "inv-x", "amount": 120}]})
    row = _get(client, tenant, "/by-number", no="INV-X")["record"]
    assert (row["status"], row["amount_paid"], row["balance_due"]) == ("Partial", 120, 180)
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-2", "contact": "Acme LLC", "amount": 180,
                                       "invoice_no": "INV-X"})
    assert _get(client, tenant, "/by-number", no="inv-x")["record"]["status"] == "Paid"
    s = _get(client, tenant, "/summary")
    assert (s["collected"], s["pending"]) == (400, 0)

    # Deleting a receipt puts the money back to collect.
    r = client.post("/api/v1/app-data?action=delete", headers=tenant, json={"collection": "payments", "record": {"ref": "RCT-2"}})
    assert r.status_code == 200, r.text
    assert _get(client, tenant, "/by-number", no="INV-X")["record"]["status"] == "Partial"


def test_supplier_payments_do_not_settle_invoices(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("DUP-1", total=100))
    _save(client, tenant, "payments", {"type": "Supplier Payment", "ref": "PAY-1", "amount": 100, "bill_no": "DUP-1"})
    assert _get(client, tenant, "/by-number", no="DUP-1")["record"]["status"] == "Issued"


def test_receipt_saved_before_its_invoice_still_counts(client, tenant):
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-EARLY", "amount": 105, "invoice_no": "LATE-1"})
    _save(client, tenant, "salesInvoices", _invoice("LATE-1", total=105))
    assert _get(client, tenant, "/by-number", no="LATE-1")["record"]["status"] == "Paid"


def test_editing_a_receipt_moves_its_money(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("E-1", total=100))
    _save(client, tenant, "salesInvoices", _invoice("E-2", total=100))
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-E", "amount": 100, "invoice_no": "E-1"})
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-E", "amount": 100, "invoice_no": "E-2"})
    assert _get(client, tenant, "/by-number", no="E-1")["record"]["status"] == "Issued"
    assert _get(client, tenant, "/by-number", no="E-2")["record"]["status"] == "Paid"


def test_bulk_deleting_receipts_reopens_invoices(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("BD-1", total=100))
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-BD", "amount": 100, "invoice_no": "BD-1"})
    r = client.post("/api/v1/app-data?action=bulk-delete", headers=tenant,
                    json={"collection": "payments", "records": [{"ref": "RCT-BD"}]})
    assert r.status_code == 200, r.text
    assert _get(client, tenant, "/by-number", no="BD-1")["record"]["status"] == "Issued"


def test_open_invoices_for_receipts(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("OP-NEW", customer="Gulf Co", total=100) | {"date": "2026-09-20"})
    _save(client, tenant, "salesInvoices", _invoice("OP-OLD", customer="Gulf Co", total=100) | {"date": "2026-09-01"})
    _save(client, tenant, "salesInvoices", _invoice("OP-PAID", customer="Gulf Co", total=100, status="Paid"))
    _save(client, tenant, "salesInvoices", _invoice("OP-OTHER", customer="Other", total=100))
    _save(client, tenant, "payments", {"type": "Customer Receipt", "ref": "RCT-OP", "amount": 40, "invoice_no": "OP-NEW"})
    docs = _get(client, tenant, "/open", customer="gulf co")["documents"]
    assert [(d["ref"], d["amount"], d["status"]) for d in docs] == [("OP-OLD", 100, "Issued"), ("OP-NEW", 60, "Partial")]


def test_salesperson_stats_and_stock_movements(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("SP-1", total=100, salesperson="Ali"))
    _save(client, tenant, "salesInvoices", _invoice("SP-2", total=50, salesperson="ali"))
    _save(client, tenant, "salesInvoices", _invoice("POS-1", total=50, source="POS"))
    assert _get(client, tenant, "/salespeople")["stats"] == {"ali": {"count": 2, "total": 150}}
    moves = _get(client, tenant, "/stock-movements")["movements"]
    assert sorted(m["reference"] for m in moves) == ["SP-1", "SP-2"]
    assert moves[0]["quantity"] == -2 and moves[0]["movement_type"] == "sale"


def test_bootstrap_no_longer_ships_invoices(client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("BOOT-1"))
    data = client.get("/api/v1/app-data", headers=tenant).json()["data"]
    assert "salesInvoices" not in data


def test_other_tenants_invoices_are_invisible(client, tenant, second_tenant_headers):
    _save(client, tenant, "salesInvoices", _invoice("MINE-1"))
    assert _get(client, second_tenant_headers, "", q="MINE-1")["total"] == 0
    r = client.get("/api/v1/app-data/sales-invoices/by-number", headers=second_tenant_headers, params={"no": "MINE-1"})
    assert r.status_code == 404


def test_columns_stamped_from_non_iso_date(db, client, tenant):
    _save(client, tenant, "salesInvoices", _invoice("DT-1") | {"date": "20/02/2026"})
    row = db.query(AppDataRecord).filter(AppDataRecord.record_key == "DT-1").one()
    assert (row.record_date, row.party, row.doc_status, row.doc_kind, row.amount) == (
        "2026-02-20", "Acme LLC", "issued", "invoice", Decimal("105.00"))
