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
