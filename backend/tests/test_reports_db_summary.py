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


def test_profit_loss_payroll_line_uses_gross_not_net_total(client, db, auth_headers):
    # A loan/advance deduction reduces what an employee takes home, not what
    # the company actually spent on payroll — summing PayrollRun.net_total
    # here understated payroll expense (and so overstated net profit) by
    # exactly the deduction amount. gross_total is the correct figure.
    from app.models import PayrollRun

    company_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    run = PayrollRun(company_id=company_id, period="2025-11", status="draft",
                      gross_total="10000.00", deductions_total="2500.00", net_total="7500.00")
    db.add(run)
    db.commit()

    response = client.get("/api/v1/reports/summary", headers=auth_headers)
    assert response.status_code == 200
    payroll_line = response.json()["profit_loss"]["payroll"]
    assert float(payroll_line) >= 10000.00
