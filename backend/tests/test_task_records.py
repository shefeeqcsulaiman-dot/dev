"""task_records (app/task_records.py) stays in step with the JSON "tasks" records for every
kind of write: app-data save / edit / delete, bulk save, a status change from ESS, and a
bulk delete; verify_company() agrees and rebuild_company() repairs a drifted row."""
import json
import uuid

from app.models import AppDataRecord, TaskRecord
from app.task_records import mirror, verify_company
from tests.test_hrms_department_scope import DEPT_A, _company_id, _delete, _login_as, _save


def _row(db, task_id):
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.collection == "tasks", AppDataRecord.record_key == task_id).first()
    return db.get(TaskRecord, rec.id) if rec else None


def test_rows_follow_every_kind_of_write(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:8]
    t1, t2 = f"TR-1-{tag}", f"TR-2-{tag}"

    assert _save(client, auth_headers, "tasks", {"id": t1, "title": "Count stock", "department": DEPT_A, "assigned_to": "E1",
                                                 "assigned_to_name": "Ali", "status": "todo", "priority": "High",
                                                 "progress": "30", "due_date": "2026-10-20T00:00:00Z", "repeat": "weekly"}).status_code == 200
    row = _row(db, t1)
    assert (row.title, row.department, row.assigned_to, row.status, row.is_open, row.priority, row.progress, row.due_date, row.repeat) == \
           ("Count stock", DEPT_A, "E1", "todo", True, "High", 30, "2026-10-20", "weekly")

    # Edit: finished statuses close it, whatever their wording.
    for status, is_open in (("Completed", False), ("Blocked", True), ("done", False)):
        assert _save(client, auth_headers, "tasks", {"id": t1, "title": "Count stock", "status": status}).status_code == 200
        assert _row(db, t1).is_open is is_open, status
    assert _row(db, t1).progress is None and _row(db, t1).due_date is None  # fields removed from the record

    # Bulk save creates and updates.
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"}, json={
        "collection": "tasks", "records": [{"id": t1, "title": "Renamed", "status": "todo"},
                                           {"id": t2, "title": "Second", "status": "in progress", "progress": 250}]})
    assert r.status_code == 200, r.text
    assert (_row(db, t1).title, _row(db, t1).is_open) == ("Renamed", True)
    assert _row(db, t2).progress == 100  # clamped to 0-100

    # A status change made from ESS (assigned to the ESS user's own employee record).
    hr = _login_as(client, db, auth_headers, cid, tag, [], ["hr_workflow:view", "hr_workflow:edit"])
    me = client.get("/api/v1/ess/me", headers=hr).json()
    assert _save(client, auth_headers, "tasks", {"id": t2, "title": "Second", "assigned_to": me["id"], "status": "todo"}).status_code == 200
    assert client.patch(f"/api/v1/ess/tasks/{t2}", headers=hr, json={"status": "done"}).status_code == 200
    assert (_row(db, t2).status, _row(db, t2).is_open, _row(db, t2).progress) == ("done", False, 100)

    assert verify_company(db, cid)

    # Delete removes the row; a bulk delete too.
    record_id = db.query(AppDataRecord.id).filter(AppDataRecord.record_key == t1).scalar()
    assert _delete(client, auth_headers, "tasks", {"id": t1}).status_code == 200
    db.expire_all()
    assert db.get(TaskRecord, record_id) is None
    record_id = db.query(AppDataRecord.id).filter(AppDataRecord.record_key == t2).scalar()
    db.query(AppDataRecord).filter(AppDataRecord.id == record_id).delete(synchronize_session=False)
    db.commit()
    assert db.get(TaskRecord, record_id) is None
    assert verify_company(db, cid)


def test_verify_spots_drift_and_rebuild_repairs_it(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    task_id = f"TR-D-{uuid.uuid4().hex[:8]}"
    assert _save(client, auth_headers, "tasks", {"id": task_id, "title": "Drift", "status": "todo"}).status_code == 200
    row = _row(db, task_id)
    db.query(TaskRecord).filter(TaskRecord.id == row.id).update({"is_open": False}, synchronize_session=False)
    db.commit()
    assert not verify_company(db, cid)
    mirror.rebuild_company(db, cid)
    db.commit()
    assert verify_company(db, cid) and _row(db, task_id).is_open is True
