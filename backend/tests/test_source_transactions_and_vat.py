from datetime import datetime, timezone
from decimal import Decimal


def test_source_transaction_validation_approval_tax_and_audit(client, auth_headers):
    created = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "sales",
            "reference": "SRC-SALES-001",
            "party_name": "Source Customer",
            "lines": [{"description": "Sale", "account_code": "3000", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201
    source = created.json()
    assert source["subtotal"] == "100.00"
    assert source["vat"] == "5.00"
    assert source["total"] == "105.00"

    validated = client.post(f"/api/v1/source-transactions/{source['id']}/validate", headers=auth_headers)
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"

    approved = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)
    assert approved.status_code == 202
    assert approved.json()["status"] == "posted"

    tax_lines = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    matching = [line for line in tax_lines if line["source_id"] == source["id"]]
    assert len(matching) == 1
    assert matching[0]["direction"] == "output"
    assert Decimal(matching[0]["tax_amount"]) == Decimal("5.00")

    audit_rows = client.get("/api/v1/audit/trail", headers=auth_headers).json()
    assert any(row["record_id"] == source["id"] and row["action"] == "approved" for row in audit_rows)
    assert any(row["record_id"] == source["id"] and row["action"] == "posted_to_ledger" for row in audit_rows)

    journals = client.get("/api/v1/journal", headers=auth_headers).json()
    matching_journals = [journal for journal in journals if journal["source_id"] == source["id"]]
    assert len(matching_journals) == 1
    journal = matching_journals[0]
    assert journal["source_module"] == "sales"
    assert sum(Decimal(line["debit"]) for line in journal["lines"]) == sum(Decimal(line["credit"]) for line in journal["lines"])

    approved_again = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)
    assert approved_again.status_code == 202
    assert approved_again.json()["status"] == "posted"
    journals_after_retry = client.get("/api/v1/journal", headers=auth_headers).json()
    assert len([journal for journal in journals_after_retry if journal["source_id"] == source["id"]]) == 1


def test_source_transaction_missing_account_rejected(client, auth_headers):
    created = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "sales",
            "reference": "SRC-MISSING-001",
            "party_name": "Source Customer",
            "lines": [{"description": "Sale", "account_code": "NOPE", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201

    approved = client.post(f"/api/v1/source-transactions/{created.json()['id']}/approve", headers=auth_headers)
    assert approved.status_code == 422
    assert "missing account" in approved.json()["detail"].lower()


def test_vat_return_reads_tax_lines(client, auth_headers):
    source = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "purchase",
            "reference": "SRC-PUR-001",
            "party_name": "Supplier",
            "lines": [{"description": "Purchase", "account_code": "4000", "quantity": "1", "unit_price": "200.00", "vat_rate": "5"}],
        },
    ).json()
    client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)

    # TaxLine.period is stamped from the transaction's actual creation date,
    # not a hardcoded value, so the current real-world period must be used.
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    vat_return = client.get(f"/api/v1/tax/vat-return?period={period}", headers=auth_headers)
    assert vat_return.status_code == 200
    payload = vat_return.json()
    assert Decimal(payload["input_vat"]) >= Decimal("10.00")

    saved_return = client.post(
        "/api/v1/tax/vat-returns",
        headers=auth_headers,
        json={"period": period, "adjustments": "1.00", "filing_status": "approved", "fta_reference_no": "FTA-QA-001"},
    )
    assert saved_return.status_code == 201
    assert Decimal(saved_return.json()["input_vat"]) >= Decimal("10.00")
    assert saved_return.json()["filing_status"] == "approved"


def test_corporate_tax_return_calculates_taxable_income(client, auth_headers):
    response = client.post(
        "/api/v1/tax/corporate-tax-returns",
        headers=auth_headers,
        json={
            "tax_period": "2024",
            "accounting_profit": "100000.00",
            "non_deductible_expenses": "1000.00",
            "exempt_income": "500.00",
            "tax_loss_adjustment": "100.00",
            "tax_rate": "9.00",
            "filing_status": "approved",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert Decimal(payload["taxable_income"]) == Decimal("100400.00")
    # Below the AED 375,000 Small Business Relief threshold — 0% applies.
    assert Decimal(payload["corporate_tax_payable"]) == Decimal("0.00")


def test_corporate_tax_return_applies_small_business_relief_threshold(client, auth_headers):
    response = client.post(
        "/api/v1/tax/corporate-tax-returns",
        headers=auth_headers,
        json={
            "tax_period": "2025",
            "accounting_profit": "500000.00",
            "non_deductible_expenses": "1000.00",
            "exempt_income": "500.00",
            "tax_loss_adjustment": "100.00",
            "tax_rate": "9.00",
            "filing_status": "approved",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert Decimal(payload["taxable_income"]) == Decimal("500400.00")
    # Only the amount above AED 375,000 is taxed: (500400 - 375000) * 9% = 11286.00
    assert Decimal(payload["corporate_tax_payable"]) == Decimal("11286.00")
