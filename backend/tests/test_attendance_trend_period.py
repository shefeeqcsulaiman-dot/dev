"""GET /attendance/trend previously could only ever answer "the last N
days ending today" -- the Attendance Calendar had no way to view a past
or future month's data since this is what it draws its day-coloring
from. Widened with an optional `period` (YYYY-MM) that returns exactly
that calendar month regardless of today's date."""
from datetime import datetime, timezone

from app.models import AttendancePunch


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def test_trend_with_period_returns_exact_month(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    db.add(AttendancePunch(
        company_id=company_id, employee_id="ATT-TREND-001", employee_name="Trend Period Test",
        punch_time=datetime(2026, 6, 15, 8, tzinfo=timezone.utc),
        punch_date="2026-06-15", direction="in", source="device",
    ))
    db.commit()

    r = client.get("/api/v1/attendance/trend?period=2026-06", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["dates"][0] == "2026-06-01"
    assert data["dates"][-1] == "2026-06-30"
    assert len(data["dates"]) == 30
    idx = data["dates"].index("2026-06-15")
    assert data["counts"][idx] == 1
    # A different day in the same month with no punches must stay 0.
    idx2 = data["dates"].index("2026-06-16")
    assert data["counts"][idx2] == 0


def test_trend_rejects_bad_period_format(client, auth_headers):
    r = client.get("/api/v1/attendance/trend?period=nope", headers=auth_headers)
    assert r.status_code == 400, r.text


def test_trend_without_period_still_uses_days(client, auth_headers):
    r = client.get("/api/v1/attendance/trend?days=10", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["dates"]) == 10
