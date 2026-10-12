"""rota_records (app/rota_records.py) stays in step with the JSON "rotaAssignments" records
for every kind of write: app-data save / edit / delete, bulk save and a bulk delete;
verify_company() agrees and rebuild_company() repairs a drifted row. The readers that moved
to it (days off, unpaid breaks) give the same answers as reading the JSON did."""
import uuid
from datetime import date

from app.models import AppDataRecord, RotaRecord
from app.rota_days import rota_break_seconds, rota_day_statuses
from app.rota_records import mirror, verify_company
from tests.test_hrms_department_scope import DEPT_A, _company_id, _delete, _save


def _row(db, key):
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.collection == "rotaAssignments",
                                         AppDataRecord.record_key == key).first()
    return db.get(RotaRecord, rec.id) if rec else None


def test_rows_follow_every_kind_of_write(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:8]
    emp = f"E-{tag}"
    a1, a2 = f"{emp}-2026-10-13", f"{emp}-2026-10-14"

    assert _save(client, auth_headers, "rotaAssignments", {
        "id": a1, "employee_id": emp, "employee_name": "Ali", "department": DEPT_A, "location": "Main",
        "date": "2026-10-13", "type": "Morning", "code": "m", "start": "09:00", "end": "17:00",
        "break_minutes": 45, "status": "Draft"}).status_code == 200
    row = _row(db, a1)
    assert (row.employee_no, row.day, row.kind, row.code, row.shift_type, row.start_time, row.end_time,
            row.break_minutes, row.status, row.department) == \
           (emp, "2026-10-13", "shift", "M", "Morning", "09:00", "17:00", 45.0, "Draft", DEPT_A)

    # Edit: marked Off; no break saved -> empty (callers fall back to the shift's).
    assert _save(client, auth_headers, "rotaAssignments", {
        "id": a1, "employee_id": emp, "date": "2026-10-13", "mark": "Off", "code": "OFF"}).status_code == 200
    row = _row(db, a1)
    assert (row.kind, row.code, row.break_minutes, row.shift_type) == ("off", "OFF", None, None)

    # Bulk save creates and updates.
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"}, json={
        "collection": "rotaAssignments", "records": [
            {"id": a1, "employee_id": emp, "date": "2026-10-13", "mark": "Leave", "code": "L"},
            {"id": a2, "employee_id": emp, "date": "2026-10-14T00:00:00Z", "code": "PH"}]})
    assert r.status_code == 200, r.text
    assert _row(db, a1).kind == "leave"
    assert (_row(db, a2).kind, _row(db, a2).day) == ("holiday", "2026-10-14")
    assert verify_company(db, cid)

    # Delete removes the row; a bulk delete too.
    record_id = db.query(AppDataRecord.id).filter(AppDataRecord.record_key == a1).scalar()
    assert _delete(client, auth_headers, "rotaAssignments", {"id": a1}).status_code == 200
    db.expire_all()
    assert db.get(RotaRecord, record_id) is None
    record_id = db.query(AppDataRecord.id).filter(AppDataRecord.record_key == a2).scalar()
    db.query(AppDataRecord).filter(AppDataRecord.id == record_id).delete(synchronize_session=False)
    db.commit()
    assert db.get(RotaRecord, record_id) is None
    assert verify_company(db, cid)


def test_days_off_and_breaks_read_from_the_typed_rows(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:8]
    me, other = f"A-{tag}", f"B-{tag}"
    # Week of Mon 2026-10-19: me works Mon (own 30 min break) and Tue (shift N: default 60),
    # is on leave Wed, Off Thu; no entries Fri-Sun -> days off. other has no rota that week.
    for day, extra in (("2026-10-19", {"code": "M", "break_minutes": 30}), ("2026-10-20", {"code": "N"}),
                       ("2026-10-21", {"mark": "Leave", "code": "L"}), ("2026-10-22", {"mark": "Off", "code": "OFF"})):
        assert _save(client, auth_headers, "rotaAssignments",
                     {"id": f"{me}-{day}", "employee_id": me, "date": day, **extra}).status_code == 200
    start, end = date(2026, 10, 19), date(2026, 10, 25)

    statuses = rota_day_statuses(db, cid, [me, other], start, end)
    assert statuses == {(me, "2026-10-21"): "leave", (me, "2026-10-22"): "off", (me, "2026-10-23"): "off",
                        (me, "2026-10-24"): "off", (me, "2026-10-25"): "off"}
    assert rota_break_seconds(db, cid, [me, other], start, end) == {(me, "2026-10-19"): 1800, (me, "2026-10-20"): 3600}


def test_verify_spots_drift_and_rebuild_repairs_it(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    key = f"RD-{uuid.uuid4().hex[:8]}"
    assert _save(client, auth_headers, "rotaAssignments",
                 {"id": key, "employee_id": "E9", "date": "2026-10-13", "code": "M"}).status_code == 200
    row = _row(db, key)
    db.query(RotaRecord).filter(RotaRecord.id == row.id).update({"kind": "off"}, synchronize_session=False)
    db.commit()
    assert not verify_company(db, cid)
    mirror.rebuild_company(db, cid)
    db.commit()
    assert verify_company(db, cid) and _row(db, key).kind == "shift"
