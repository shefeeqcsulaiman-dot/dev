"""Super Admin timeline and alerts (app/observability.py)."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app import observability, server_errors
from app.config import get_settings
from tests.test_module_permissions import _make_restricted_company


def _sa(client, db):
    return _make_restricted_company(client, db, f"ob-{uuid4().hex[:4]}", ["sales"])[2]


def test_timeline_has_a_day_in_five_minute_steps_with_errors_placed(client, db):
    sa = _sa(client, db)
    server_errors.clear()
    server_errors.record(status=500, method="POST", path="/api/v1/app-data", query="action=save",
                         exc=RuntimeError("boom"), authorization=None, build="test")
    from app.routers import superadmin
    superadmin._local_cache.clear()
    r = client.get("/api/v1/superadmin/timeline?hours=24", headers=sa)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["step_minutes"] == 5
    assert 288 <= len(d["points"]) <= 289
    assert sum(p["errors"] for p in d["points"]) == 1
    assert d["points"][-1]["errors"] + d["points"][-2]["errors"] == 1   # in the latest steps
    server_errors.clear()


def test_overloads_raise_one_alert_per_cooldown(db, monkeypatch):
    server_errors.clear()
    observability._local_alerts.clear()
    observability._local_cooldown.clear()
    for _ in range(3):
        server_errors.record(status=503, method="GET", path="/api/v1/reports/dashboard", query="",
                             exc=TimeoutError("pool"), authorization=None, build="test")
    raised = observability.check_alerts(db)
    assert [a["kind"] for a in raised] == ["overload"] and raised[0]["level"] == "red"
    assert observability.check_alerts(db) == []          # same problem, inside 30 minutes: not again
    assert observability.recent_alerts()[0]["kind"] == "overload"
    server_errors.clear()


def test_no_alerts_when_all_is_well(db):
    server_errors.clear()
    observability._local_cooldown.clear()
    kinds = [a["kind"] for a in observability.check_alerts(db)]
    assert "overload" not in kinds and "server_errors" not in kinds and "db_down" not in kinds


def test_alerts_endpoint_and_test_alert(client, db, monkeypatch):
    sa = _sa(client, db)
    sent = []
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://example.invalid/hook")
    monkeypatch.setattr(observability, "_send", lambda alert: sent.append(alert))
    r = client.post("/api/v1/superadmin/alerts/test", headers=sa)
    assert r.status_code == 200 and r.json()["channels"]["webhook"] is True
    assert sent and sent[0]["kind"] == "test"
    body = client.get("/api/v1/superadmin/alerts", headers=sa).json()
    assert body["alerts"][0]["kind"] == "test" and body["channels"] == {"email": False, "webhook": True}


def test_platform_health_and_system_health_split_server_errors(client, db):
    sa = _sa(client, db)
    server_errors.clear()
    server_errors.record(status=503, method="GET", path="/x", query="", exc=TimeoutError("p"), authorization=None)
    server_errors.record(status=500, method="GET", path="/y", query="", exc=KeyError("k"), authorization=None)
    d = client.get("/api/v1/superadmin/system-health?days=14", headers=sa).json()
    assert d["total_server_errors"] == 1 and d["total_overloads"] == 1
    assert len(d["server_error_trend"]) == 14 and d["server_error_trend"][-1]["count"] == 1
    server_errors.clear()
