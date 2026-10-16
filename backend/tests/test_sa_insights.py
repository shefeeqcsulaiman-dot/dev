"""Super Admin overview panels: live platform health (database, errors in the last hour, load,
people online, builds per server copy) and insights (busiest hours, companies needing
attention, weekly growth, module adoption)."""
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app import active_users, request_metrics
from app.models import AppDataRecord, Company, User
from tests.test_module_permissions import _make_restricted_company


def _insights(client, sa):
    from app.routers import superadmin
    superadmin._local_cache.clear()
    r = client.get("/api/v1/superadmin/insights", headers=sa)
    assert r.status_code == 200, r.text
    return r.json()


def test_platform_health_reports_live_figures(client, db):
    _, _, sa = _make_restricted_company(client, db, f"ph-{uuid4().hex[:4]}", ["sales"])
    r = client.get("/api/v1/superadmin/platform-health", headers=sa)
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["db_ok"] is True and h["db_latency_ms"] >= 0
    assert h["status"]["level"] in ("green", "amber", "red")
    assert set(h["errors_1h"]) == {"overloads", "errors"}
    assert h["deploy"]["copies"] >= 1 and sum(h["deploy"]["builds"].values()) == h["deploy"]["copies"]
    assert h["online_now"] >= 1   # the company admin who just signed in
    if db.get_bind().dialect.name == "postgresql":
        assert h["db_connections"]["max"] > 0


def test_attention_lists_expiring_and_unused_companies(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"at-{tag}", ["sales"])
    soon = (date.today() + timedelta(days=5)).isoformat()
    company = db.get(Company, cid)
    company.subscription_expires_at = soon
    company.created_at = datetime.now(UTC) - timedelta(days=30)
    for u in db.query(User).filter(User.company_id == cid).all():
        u.last_activity = datetime.now(UTC) - timedelta(days=10)
    db.commit()

    items = [i for i in _insights(client, sa)["attention"] if i["company_id"] == cid]
    kinds = {i["kind"]: i for i in items}
    assert kinds["expiring"]["severity"] == "amber" and "5 day" in kinds["expiring"]["detail"]
    assert kinds["inactive"]["detail"] == "No activity for 10 days"


def test_growth_and_adoption_count_this_weeks_records(client, db):
    tag = uuid4().hex[:4]
    cid, _, sa = _make_restricted_company(client, db, f"gr-{tag}", ["sales"])
    for i in range(3):
        db.add(AppDataRecord(company_id=cid, collection="salesInvoices", record_key=f"GR-{tag}-{i}", payload="{}"))
    db.commit()

    d = _insights(client, sa)
    g = d["growth"]
    assert len(g["weeks"]) == 8 and g["companies"][-1] >= 1 and g["records"][-1] >= 3
    row = next(r for r in d["adoption"]["rows"] if r["company_id"] == cid) if any(
        r["company_id"] == cid for r in d["adoption"]["rows"]) else None
    if row is not None:   # top 25 only: present unless 25 busier companies exist
        assert row["modules"]["sales"] >= 3
    assert d["adoption"]["companies_using"]["sales"] >= 1
    assert len(d["busiest_hours"]) == 168


def test_hourly_peaks_cover_a_week():
    active_users._local_peaks.clear()
    now = datetime.now(UTC).timestamp()
    active_users.record_sample(7, now - 3 * 24 * 3600)
    active_users.record_sample(3, now)
    series = active_users.hourly_peaks(168, now)
    assert len(series) == 168
    assert [p["peak"] for p in series if p["peak"] is not None] == [7, 3]


def test_builds_without_redis_is_this_process():
    b = request_metrics.builds()
    assert b["copies"] >= 1 and b["builds"]
