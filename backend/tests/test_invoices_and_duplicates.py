from decimal import Decimal

from app.models import CorporateTaxRecord, JournalEntry, SourceTransaction


def test_invoice_vat_calculation(client, auth_headers):
    response = client.post(
        "/api/v1/invoices",
        headers=auth_headers,
        json={
            "customer_name": "VAT Customer",
            "invoice_number": "VAT-INV-001",
            "lines": [{"description": "Service", "quantity": "2", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["subtotal"] == "200.00"
    assert payload["vat"] == "10.00"
    assert payload["total"] == "210.00"


def test_invoice_posts_to_accounting_and_corporate_tax(client, auth_headers, db):
    db.expire_all()
    existing_corporate_tax = db.query(CorporateTaxRecord).order_by(CorporateTaxRecord.created_at.desc()).first()
    previous_profit = existing_corporate_tax.accounting_profit if existing_corporate_tax else Decimal("0.00")

    response = client.post(
        "/api/v1/invoices",
        headers=auth_headers,
        json={
            "customer_name": "Connected Customer",
            "invoice_number": "ACC-INV-001",
            "lines": [{"description": "Consulting", "quantity": "1", "unit_price": "500.00", "vat_rate": "5"}],
        },
    )

    assert response.status_code == 201
    db.expire_all()
    source = (
        db.query(SourceTransaction)
        .filter(SourceTransaction.module == "sales", SourceTransaction.reference == "ACC-INV-001")
        .one()
    )
    assert source.status == "posted"
    assert source.subtotal == Decimal("500.00")

    journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.source_module == "sales", JournalEntry.source_id == source.id)
        .one()
    )
    assert journal.status == "posted"

    corporate_tax = db.query(CorporateTaxRecord).order_by(CorporateTaxRecord.created_at.desc()).first()
    assert corporate_tax is not None
    assert corporate_tax.status == "calculated"
    assert corporate_tax.accounting_profit == previous_profit + Decimal("500.00")


def test_duplicate_invoice_number_rejected_per_tenant(client, auth_headers):
    body = {
        "customer_name": "Duplicate Customer",
        "invoice_number": "DUP-INV-001",
        "lines": [{"description": "Service", "quantity": "1", "unit_price": "50.00", "vat_rate": "5"}],
    }
    first = client.post("/api/v1/invoices", headers=auth_headers, json=body)
    second = client.post("/api/v1/invoices", headers=auth_headers, json=body)

    assert first.status_code == 201
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]
