def test_ai_assistant_answers_with_company_context(client, auth_headers):
    response = client.post(
        "/api/v1/ai/assist",
        headers=auth_headers,
        json={"question": "What should I check before VAT filing?"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "VAT readiness" in data["answer"]
    assert data["context"]["invoice_count"] >= 0
    assert "direct-post" in " ".join(data["controls"])


def test_ai_transaction_review_does_not_post_or_approve(client, auth_headers):
    response = client.post(
        "/api/v1/ai/validate-transaction",
        headers=auth_headers,
        json={
            "source": {
                "module": "purchase",
                "reference": "AI-REVIEW-1",
                "party_name": "Supplier LLC",
                "lines": [
                    {
                        "description": "Office rent",
                        "account_code": "9999",
                        "quantity": "1",
                        "unit_price": "1000.00",
                        "vat_rate": "5",
                    }
                ],
            },
            "supplier_trn": "",
            "evidence_present": False,
            "tax_treatment": "standard",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["context"]["posting_allowed"] is False
    assert any("tax evidence" in item.lower() for item in data["suggested_actions"])
    assert any("9999 is missing" in item for item in data["suggested_actions"])


def test_ai_transaction_vat_check_uses_company_vat_rate(client, auth_headers):
    """vat_issues() used to hardcode "differs from 5%" regardless of the
    company's configured VAT rate — a correctly-computed transaction for a
    company on a non-default rate would get spuriously flagged as an
    anomaly. Set the company to 15% and submit a transaction whose VAT is
    correctly 15% of the line total; no VAT-rate issue should be raised."""
    updated = client.put("/api/v1/companies/current", headers=auth_headers, json={"vat_rate": "15.00"})
    assert updated.status_code == 200

    response = client.post(
        "/api/v1/ai/validate-transaction",
        headers=auth_headers,
        json={
            "source": {
                "module": "sales",
                "reference": "AI-VATRATE-1",
                "party_name": "Customer LLC",
                "lines": [
                    {
                        "description": "Consulting revenue",
                        "account_code": "3000",
                        "quantity": "1",
                        "unit_price": "1000.00",
                        "vat_rate": "15",
                    }
                ],
            },
            "supplier_trn": "100234567800003",
            "evidence_present": True,
            "tax_treatment": "standard",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert not any("differs from" in item.lower() for item in data["suggested_actions"]), data["suggested_actions"]


def test_ai_exception_explainer_is_read_only(client, auth_headers):
    response = client.post(
        "/api/v1/ai/explain-exception",
        headers=auth_headers,
        json={
            "module": "Purchases",
            "category": "Duplicate invoice",
            "severity": "high",
            "source_record": "BILL-100",
            "message": "Invoice/reference BILL-100 appears 2 times",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert "duplicate reference" in data["answer"].lower()
    assert "unchanged" in " ".join(data["controls"]).lower()
