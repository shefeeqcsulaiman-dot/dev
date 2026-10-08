"""The 5-minute jobs only touch what has something to do: the stale check-out sweep
finds stale sessions in one query (not one per open session), and the BioTime sync queues
one task per active BioTime server."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.models import AttendanceSession, BiometricDevice, Company, Employee, EmployeeLocationLog


def _company_with_employee(db):
    tag = uuid4().hex[:8]
    company = Company(name=f"Sched Co {tag}", trn=f"SCHED-{tag}")
    db.add(company)
    db.flush()
    emp = Employee(company_id=company.id, employee_no=f"S-{tag}", full_name="Sched Employee")
    db.add(emp)
    db.flush()
    return company, emp


def _session(db, company, emp, minutes_ago, status="open"):
    # One open session per employee is enforced (a unique index), so each session gets its own.
    emp = Employee(company_id=company.id, employee_no=f"S-{uuid4().hex[:8]}", full_name="Sched Employee")
    db.add(emp)
    db.flush()
    s = AttendanceSession(company_id=company.id, employee_id=emp.id, status=status,
                          check_in=datetime.now(UTC) - timedelta(minutes=minutes_ago))
    db.add(s)
    db.flush()
    return s


def _ping(db, s, minutes_ago):
    db.add(EmployeeLocationLog(company_id=s.company_id, employee_id=s.employee_id, session_id=s.id,
                               latitude=Decimal("25.2"), longitude=Decimal("55.3"),
                               created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))


def test_stale_sessions_close_and_live_ones_stay_open(db):
    from app import worker

    company, emp = _company_with_employee(db)
    no_ping_old = _session(db, company, emp, 60)          # no ping, checked in an hour ago -> close
    old_ping = _session(db, company, emp, 90)             # last ping 30 min ago -> close
    _ping(db, old_ping, 80)
    _ping(db, old_ping, 30)
    recent_ping = _session(db, company, emp, 120)         # pinged 2 min ago -> stays open
    _ping(db, recent_ping, 2)
    just_started = _session(db, company, emp, 3)          # checked in 3 min ago -> stays open
    closed_already = _session(db, company, emp, 300, status="closed")
    db.commit()

    closed = worker.auto_checkout_stale_sessions()

    assert closed >= 2
    db.expire_all()
    got = {s.id: (s.status, s.auto_checkout) for s in db.query(AttendanceSession).filter(AttendanceSession.company_id == company.id)}
    assert got[no_ping_old.id] == ("closed", True)
    assert got[old_ping.id] == ("closed", True)
    assert got[recent_ping.id][0] == "open"
    assert got[just_started.id][0] == "open"
    assert got[closed_already.id] == ("closed", False)
    assert db.get(AttendanceSession, no_ping_old.id).check_out is not None


def test_biotime_sync_queues_only_active_biotime_servers(db, monkeypatch):
    from app import biotime_sync, worker

    company, _ = _company_with_employee(db)
    active = BiometricDevice(company_id=company.id, name="BT active", device_type="ZKTeco BioTime Server", status="active")
    paused = BiometricDevice(company_id=company.id, name="BT paused", device_type="ZKTeco BioTime Server", status="inactive")
    other = BiometricDevice(company_id=company.id, name="Push device", device_type="ZKTeco", status="active")
    failing = BiometricDevice(company_id=company.id, name="BT down", device_type="ZKTeco BioTime Server", status="active")
    db.add_all([active, paused, other, failing])
    db.commit()
    synced = []

    def fake_sync(session, device):
        if device.id == failing.id:
            raise ConnectionError("server unreachable")
        synced.append(device.id)
        return 3

    monkeypatch.setattr(biotime_sync, "sync_biotime_device", fake_sync)
    queued = worker.sync_biotime_devices()

    assert active.id in synced
    assert paused.id not in synced and other.id not in synced
    assert queued >= 2  # active + failing (one failure doesn't stop the others)
