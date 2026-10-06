"""Sales people: a company-level list (salesPeople) picked on sales invoices."""
import uuid


def _save(client, headers, collection, record):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                       json={"collection": collection, "record": record})


def test_sales_person_saved_and_returned_on_load(client, auth_headers):
    sp_id = f"SP-{uuid.uuid4().hex[:8]}"
    r = _save(client, auth_headers, "salesPeople", {"id": sp_id, "name": "Ahmed Rahman", "phone": "+971501234567", "status": "Active"})
    assert r.status_code == 200, r.text
    boot = client.get("/api/v1/app-data", headers=auth_headers).json()["data"]
    assert any(p.get("id") == sp_id and p.get("name") == "Ahmed Rahman" for p in boot.get("salesPeople", []))


def test_sales_invoice_keeps_its_sales_person(client, auth_headers):
    inv_no = f"INV-SP-{uuid.uuid4().hex[:6]}"
    r = _save(client, auth_headers, "salesInvoices", {
        "invoice_no": inv_no, "customer": "SP Test Customer", "date": "2026-10-01", "salesperson": "Ahmed Rahman",
        "subtotal": 100, "vat_amount": 5, "total": 105, "status": "Draft",
        "lines": [{"description": "Item", "qty": 1, "price": 100, "amount": 100}],
    })
    assert r.status_code == 200, r.text
    rows = client.get("/api/v1/app-data/records/salesInvoices", headers=auth_headers, params={"limit": 500}).json()
    rows = rows.get("records", rows) if isinstance(rows, dict) else rows
    saved = next(x for x in rows if x.get("invoice_no") == inv_no)
    assert saved.get("salesperson") == "Ahmed Rahman"
