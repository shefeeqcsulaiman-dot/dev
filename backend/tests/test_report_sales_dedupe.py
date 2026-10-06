"""app_sales_invoice_records(): app-data invoices that already posted a real Invoice are
left out (now in SQL); credit notes and unposted ones stay, exactly as before."""
from uuid import uuid4

import pytest

from app.models import AppDataRecord
from app.routers.reports import app_sales_invoice_records
from tests.conftest import ensure_user


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"sd-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"95{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user.company_id


def test_posted_invoices_are_left_out_credit_notes_and_odd_rows_kept(client, db, tenant):
    headers, cid = tenant
    for no in ("SD-1", "SD-2"):
        r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "salesInvoices", "record": {
            "invoice_no": no, "customer": "C", "date": "2026-09-01", "status": "Issued",
            "lines": [{"description": "x", "qty": 1, "unit_price": 100, "vat_rate": 5}]}})
        assert r.status_code == 200, r.text
    # A negative POS credit note: saved to app-data only, never posted as an Invoice.
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "salesInvoices", "record": {
        "invoice_no": "SD-CN-1", "customer": "C", "date": "2026-09-02", "status": "Return", "document_type": "Sales Return",
        "subtotal": -50, "vat_amount": -2.5, "total": -52.5}})
    assert r.status_code == 200, r.text
    # Rows the SQL shortcut must not decide on: no invoice_no (matched by invoice_number), and a
    # stored key that equals a posted number while the document's own number is different.
    db.add(AppDataRecord(company_id=cid, collection="salesInvoices", record_key="X-1",
                         payload='{"invoice_number": "SD-1", "customer": "C", "total": 1}'))
    db.add(AppDataRecord(company_id=cid, collection="salesInvoices", record_key="SD-2",
                         payload='{"id": "SD-2", "invoice_number": "OTHER-9", "customer": "C", "total": 1}'))
    db.commit()
    kept = sorted(r.get("invoice_no") or r.get("invoice_number") for r in app_sales_invoice_records(db, cid))
    assert kept == ["OTHER-9", "SD-CN-1"]
