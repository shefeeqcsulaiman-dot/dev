"""Imported sales lines that carry only an amount (no unit price) used to be re-totalled by the
server as zero — the invoice then posted no revenue/VAT for them."""
import base64
from decimal import Decimal
from uuid import uuid4

from app.models import Invoice


def test_amount_only_line_posts_its_amount(client, db, auth_headers):
    no = f"UPF-{uuid4().hex[:6]}"
    r = client.post("/api/v1/app-data?action=save", headers=auth_headers, json={"collection": "salesInvoices", "record": {
        "invoice_no": no, "customer": "Amount Only", "date": "2026-10-01", "status": "Issued",
        "lines": [{"description": "Consulting", "qty": 2, "total": 300}]}})
    assert r.status_code == 200, r.text
    db.expire_all()
    inv = db.query(Invoice).filter(Invoice.invoice_number == no).one()
    assert inv.subtotal == Decimal("300.00"), inv.subtotal


def test_csv_import_fills_unit_price_from_amount(client, auth_headers):
    no = f"UPF-CSV-{uuid4().hex[:6]}"
    csv = f"invoice_no,customer,date,description,qty,amount\n{no},Amount Only,2026-10-01,Consulting,4,200\n"
    r = client.post("/api/v1/app-data?action=invoices.import", headers=auth_headers, json={"file": {
        "name": "s.csv", "base64": "data:text/csv;base64," + base64.b64encode(csv.encode()).decode()}})
    line = r.json()["invoices"][0]["lines"][0]
    assert line["unit_price"] == 50 and line["total"] == 200, line
