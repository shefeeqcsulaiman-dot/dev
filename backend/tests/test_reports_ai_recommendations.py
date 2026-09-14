from decimal import Decimal

from app.routers.reports import anomaly_rows, suggested_actions


def _areas(rows):
    return [r["area"] for r in rows]


def test_anomaly_rows_flags_negative_net_profit_as_high_impact():
    rows = anomaly_rows(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
        net_profit=Decimal("-5000"),
    )
    profitability = next(r for r in rows if r["area"] == "Profitability")
    assert profitability["impact"] == "High"
    assert "5,000.00" in profitability["signal"] or "5000.00" in profitability["signal"]


def test_anomaly_rows_flags_low_gross_margin():
    rows = anomaly_rows(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
        gross_margin=Decimal("8.50"), revenue=Decimal("10000"),
    )
    assert "Margins" in _areas(rows)


def test_anomaly_rows_does_not_flag_margin_when_revenue_is_zero():
    # gross_margin defaults to 0.00 when there's no revenue at all -- that
    # is a "no data" state, not a genuinely thin margin, so it must not
    # be reported as an anomaly.
    rows = anomaly_rows(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    assert "Margins" not in _areas(rows)


def test_anomaly_rows_flags_high_payroll_to_revenue_ratio():
    rows = anomaly_rows(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("6000"),
        revenue=Decimal("10000"),
    )
    payroll_rows = [r for r in rows if r["area"] == "Payroll"]
    assert any("60%" in r["signal"] or "60.00%" in r["signal"] for r in payroll_rows)


def test_anomaly_rows_flags_payables_far_exceeding_receivables():
    rows = anomaly_rows(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
        ap_total=Decimal("30000"), ar_total=Decimal("10000"),
    )
    assert "Cash Flow" in _areas(rows)


def test_anomaly_rows_flags_negative_working_capital():
    rows = anomaly_rows(
        Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
        working_capital=Decimal("-1500"),
    )
    liquidity = next(r for r in rows if r["area"] == "Liquidity")
    assert liquidity["impact"] == "High"


def test_anomaly_rows_no_signals_falls_back_to_placeholder():
    rows = anomaly_rows(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"))
    assert len(rows) == 1
    assert rows[0]["area"] == "Reports"


def test_suggested_actions_mentions_net_loss():
    actions = suggested_actions(
        Decimal("0"), Decimal("0"), Decimal("0"),
        net_profit=Decimal("-2500"),
    )
    assert any("net loss" in a.lower() for a in actions)


def test_suggested_actions_mentions_thin_margin():
    actions = suggested_actions(
        Decimal("0"), Decimal("0"), Decimal("0"),
        gross_margin=Decimal("5"), revenue=Decimal("10000"),
    )
    assert any("gross margin" in a.lower() for a in actions)


def test_suggested_actions_mentions_negative_working_capital():
    actions = suggested_actions(
        Decimal("0"), Decimal("0"), Decimal("0"),
        working_capital=Decimal("-800"),
    )
    assert any("working capital" in a.lower() for a in actions)


def test_suggested_actions_healthy_company_still_reviews_vat():
    # Unconditional VAT reminder must still be present alongside new checks
    # when nothing else is wrong.
    actions = suggested_actions(
        Decimal("0"), Decimal("0"), Decimal("0"),
        net_profit=Decimal("5000"), gross_margin=Decimal("40"), revenue=Decimal("10000"),
    )
    assert any("net vat payable" in a.lower() for a in actions)
    assert not any("net loss" in a.lower() for a in actions)
    assert not any("gross margin is thin" in a.lower() for a in actions)
