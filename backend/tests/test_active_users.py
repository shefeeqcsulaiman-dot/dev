"""Super Admin "Active users": each login's last request time (users / employees / branch
logins, at most once a minute) gives online-now (15 min) and active 24 h / 7 / 30 days
counts, the busiest companies and the peak of the last 24 hours."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app import active_users
from app.auth_principal import touch_user_activity
from app.models import Employee, User
from app.security import create_access_token
from tests.test_ess_team_today_and_holidays import _ess_login
from tests.test_module_permissions import _make_restricted_company


def _summary(client, sa):
    from app.routers import superadmin
    superadmin._local_cache.clear()
    r = client.get("/api/v1/superadmin/active-users", headers=sa)
    assert r.status_code == 200, r.text
    return r.json()


def test_active_users_counts_logins_by_window_and_company(client, db):
    tag = uuid4().hex[:4]
    cid, admin_headers, sa = _make_restricted_company(client, db, f"au-{tag}", ["sales", "hrms", "ess"])
    _, ess_headers = _ess_login(client, db, admin_headers, cid, f"AU{tag}", f"au.{tag}")

    assert client.get("/api/v1/auth/whoami", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/ess/me", headers=ess_headers).status_code == 200

    db.expire_all()
    admin = db.query(User).filter(User.company_id == cid, User.role != "superadmin").first()
    emp = db.query(Employee).filter(Employee.company_id == cid).first()
    assert admin.last_activity is not None and emp.last_activity is not None

    # a second company user, last active 3 days ago: in 7 / 30 days, not online or 24 h
    old = User(company_id=cid, email=f"old-{tag}@example.com", full_name="Old User", password_hash="x",
               role="viewer", last_activity=datetime.now(UTC) - timedelta(days=3))
    db.add(old)
    db.commit()

    body = _summary(client, sa)
    mine = next(e for e in body["by_company"] if e["company_id"] == cid)
    assert mine["online"] == 2 and mine["last_24h"] == 2
    assert mine["users"] == 1 and mine["employees"] == 1

    counts = body["counts"]
    assert counts["total"]["online"] >= 2
    assert counts["users"]["last_7d"] >= counts["users"]["last_24h"] + 1
    for kind in ("users", "employees", "branches", "total"):
        c = counts[kind]
        assert c["online"] <= c["last_24h"] <= c["last_7d"] <= c["last_30d"]
    assert body["peak_24h"]["count"] >= counts["total"]["online"]   # this call sampled it

    # The Super Admin's own requests never count as a company user.
    sa_user_ids = {u.id for u in db.query(User).filter(User.role == "superadmin").all()}
    assert not any(e["company_id"] in {u.company_id for u in db.query(User).filter(User.id.in_(sa_user_ids))}
                   for e in body["by_company"])


def test_impersonation_is_not_the_user_being_active(client, db):
    tag = uuid4().hex[:4]
    cid, _, _ = _make_restricted_company(client, db, f"ai-{tag}", ["sales"])
    user = db.query(User).filter(User.company_id == cid, User.role != "superadmin").first()
    user.last_activity = None
    db.commit()

    touch_user_activity(db, user, create_access_token(user.id, impersonated_by="someone"))
    db.refresh(user)
    assert user.last_activity is None

    touch_user_activity(db, user, create_access_token(user.id))
    db.refresh(user)
    assert user.last_activity is not None


def test_active_users_needs_superadmin(client, auth_headers):
    assert client.get("/api/v1/superadmin/active-users", headers=auth_headers).status_code == 403


def test_peak_keeps_the_highest_sample_of_each_hour():
    active_users._local_peaks.clear()
    now = datetime.now(UTC).timestamp()
    active_users.record_sample(5, now - 60)
    active_users.record_sample(9, now - 30)
    active_users.record_sample(4, now)
    assert active_users.peak_last_24h(now)["count"] == 9
    active_users.record_sample(12, now - 25 * 3600)          # older than 24 h: not the peak
    assert active_users.peak_last_24h(now)["count"] == 9
