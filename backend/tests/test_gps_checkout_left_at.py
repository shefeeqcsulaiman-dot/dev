"""Check-out at the time the employee left (left_at), sent by the mobile app's automatic
check-out when the phone was offline as its owner walked out. The phone's clock may only make
the day shorter: a time in the future, before the check-in or over 12 hours old is ignored."""
from datetime import UTC, datetime, timedelta

from app.models import AttendanceSession
from tests.test_gps_checkin import CLINIC, NEAR_CLINIC, _checkin, _employee_with_sites, _gps_events


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _session(db, emp):
    db.expire_all()
    return db.query(AttendanceSession).filter(AttendanceSession.employee_id == emp.id).order_by(AttendanceSession.check_in.desc()).first()


def _backdate_check_in(db, emp, hours):
    s = _session(db, emp)
    s.check_in = datetime.now(UTC) - timedelta(hours=hours)
    db.commit()
    return s.check_in if s.check_in.tzinfo else s.check_in.replace(tzinfo=UTC)


def _as_utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def test_left_at_ends_the_day_when_the_employee_left(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, NEAR_CLINIC).status_code == 200
    _backdate_check_in(db, emp, hours=9)
    left = datetime.now(UTC) - timedelta(minutes=40)
    r = client.post("/api/v1/hr/check-out", headers=h, json={"latitude": 25.26, "longitude": 55.30, "left_at": _iso(left)})
    assert r.status_code == 200, r.text
    s = _session(db, emp)
    assert s.status == "closed"
    assert abs((_as_utc(s.check_out) - left).total_seconds()) < 1
    # The attendance record (reports, payroll) gets the same time.
    out = [e for e in _gps_events(client, h) if e["direction"] == "out"]
    assert len(out) == 1


def test_left_at_alone_without_a_position_is_accepted(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, NEAR_CLINIC).status_code == 200
    _backdate_check_in(db, emp, hours=3)
    left = datetime.now(UTC) - timedelta(minutes=10)
    assert client.post("/api/v1/hr/check-out", headers=h, json={"left_at": _iso(left)}).status_code == 200
    assert abs((_as_utc(_session(db, emp).check_out) - left).total_seconds()) < 1


def test_implausible_left_at_is_ignored(client, db, auth_headers):
    cases = {
        "future": lambda start: datetime.now(UTC) + timedelta(hours=2),           # would add hours
        "before check-in": lambda start: start - timedelta(minutes=5),
        "too old": lambda start: datetime.now(UTC) - timedelta(hours=13),
    }
    for name, make in cases.items():
        _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
        assert _checkin(client, h, NEAR_CLINIC).status_code == 200
        start = _backdate_check_in(db, emp, hours=20 if name == "too old" else 2)
        before = datetime.now(UTC)
        r = client.post("/api/v1/hr/check-out", headers=h, json={"left_at": _iso(make(start))})
        assert r.status_code == 200, (name, r.text)
        out = _as_utc(_session(db, emp).check_out)
        assert out >= before - timedelta(seconds=1), (name, out, before)


def test_web_portal_check_out_without_body_still_works(client, db, auth_headers):
    _cid, emp, h = _employee_with_sites(client, db, auth_headers, [CLINIC])
    assert _checkin(client, h, NEAR_CLINIC).status_code == 200
    assert client.post("/api/v1/hr/check-out", headers=h, json={}).status_code == 200
    assert _session(db, emp).status == "closed"
