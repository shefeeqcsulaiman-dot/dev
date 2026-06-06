def test_report_summary_includes_db_backed_extended_sections(client, auth_headers):
    response = client.get("/api/v1/reports/summary", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    for key in ("dashboard", "vat", "profit_loss", "balance_sheet", "trial_balance", "aging"):
        assert key in data
    for key in ("corporate", "assets", "budget_cash", "control"):
        assert key in data

    assert "tax_rows" in data["corporate"]
    assert "fixed_assets" in data["assets"]
    assert "budget_rows" in data["budget_cash"]
    assert "audit" in data["control"]
    assert "database records" in data["ai"]["report_text"].lower()
