"""Sales invoice import duplicate protection: invoices.import flags numbers the company
already has (whole history, not just what a browser loaded), and save with create_only
refuses -- instead of overwriting -- an invoice whose number is already taken."""
import base64
from uuid import uuid4

from app.models import Invoice


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _save(client, headers, record, create_only=False):
    body = {"collection": "salesInvoices", "record": record}
    if create_only:
        body["create_only"] = True
    return client.post("/api/v1/app-data?action=save", headers=headers, json=body)


def _import_csv(client, headers, rows):
    csv_text = "invoice_no,customer,date,description,qty,unit_price\n" + "\n".join(rows) + "\n"
    file = {"name": "sales.csv", "base64": "data:text/csv;base64," + base64.b64encode(csv_text.encode()).decode()}
    r = client.post("/api/v1/app-data?action=invoices.import", headers=headers, json={"file": file})
    assert r.status_code == 200, r.text
    return {inv["invoice_no"]: inv for inv in r.json()["invoices"]}


def test_import_flags_numbers_already_in_records_or_invoice_table(client, db, auth_headers):
    tag = uuid4().hex[:6].upper()
    saved_no, legacy_no, new_no = f"SI-{tag}-1", f"SI-{tag}-2", f"SI-{tag}-3"
    assert _save(client, auth_headers, {"invoice_no": saved_no, "customer": "Acme", "date": "2026-09-01", "status": "Draft"}).status_code == 200
    # a number that only exists in the Invoice table (e.g. created via the REST API)
    db.add(Invoice(company_id=_company_id(client, auth_headers), invoice_number=legacy_no, customer_name="Old Co"))
    db.commit()

    found = _import_csv(client, auth_headers, [
        f"{saved_no.lower()},Acme,2026-09-02,Item,1,100",   # case differs -- still the same invoice
        f"{legacy_no},Beta,2026-09-02,Item,1,100",
        f"{new_no},Gamma,2026-09-02,Item,1,100",
    ])
    assert found[saved_no.lower()].get("already_in_db") is True
    assert found[legacy_no].get("already_in_db") is True
    assert not found[new_no].get("already_in_db")


def test_create_only_save_refuses_existing_number_without_overwriting(client, auth_headers):
    tag = uuid4().hex[:6].upper()
    number = f"SI-{tag}-9"
    original = {"invoice_no": number, "customer": "Original Customer", "date": "2026-09-01", "status": "Draft", "subtotal": 100}
    assert _save(client, auth_headers, original, create_only=True).status_code == 200

    imported = {**original, "customer": "Imported Customer", "subtotal": 999}
    r = _save(client, auth_headers, imported, create_only=True)
    assert r.status_code == 409, r.text
    assert _save(client, auth_headers, {**imported, "invoice_no": number.lower()}, create_only=True).status_code == 409

    records = client.get("/api/v1/app-data/records/salesInvoices?limit=500", headers=auth_headers).json()["records"]
    stored = next(rec for rec in records if rec.get("invoice_no") == number)
    assert stored["customer"] == "Original Customer"

    # a normal save (editing an existing invoice) still updates it
    assert _save(client, auth_headers, {**original, "customer": "Edited Customer"}).status_code == 200
