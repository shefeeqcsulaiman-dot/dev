"""Edit-conflict check on app-data saves.

Saving a record used to overwrite the whole stored JSON with whatever the browser sent,
so when two people edited the same record the later save silently wiped the earlier one.
Records now carry a version ("_v" in what the browser gets); a save based on an older
version is refused (409) when someone else saved in between, and goes through as before
when there is no version or the same person saved last."""
import json
import uuid

from app.models import AppDataRecord
from tests.test_hrms_department_scope import DEPT_A, _company_id, _login_as, _mk_employee, _save

TASK_PERMS = ["hr_workflow:view", "hr_workflow:edit", "hr_workflow:delete", "employees:view"]


def _task(client, headers, task_id):
    rows = client.get("/api/v1/app-data/records/tasks", headers=headers).json()
    rows = rows if isinstance(rows, list) else rows.get("records", [])
    return next(r for r in rows if r.get("id") == task_id)


def _stored(db, task_id):
    db.expire_all()
    row = db.query(AppDataRecord).filter(AppDataRecord.collection == "tasks", AppDataRecord.record_key == task_id).one()
    return row, json.loads(row.payload)


def _setup(client, db, auth_headers):
    tag = uuid.uuid4().hex[:8]
    hr = _login_as(client, db, auth_headers, _company_id(client, auth_headers), tag, [], TASK_PERMS)
    emp = _mk_employee(client, auth_headers, db, f"EC-{tag}", DEPT_A)
    task_id = f"EC-T-{tag}"
    r = _save(client, auth_headers, "tasks", {"id": task_id, "title": "Original", "assigned_to": emp.id, "status": "todo"})
    assert r.status_code == 200, r.text
    assert r.json()["_v"] == 1
    return tag, hr, task_id


def test_stale_save_after_someone_elses_change_is_refused(client, db, auth_headers):
    tag, hr, task_id = _setup(client, db, auth_headers)
    admin_copy = _task(client, auth_headers, task_id)
    hr_copy = _task(client, hr, task_id)
    assert admin_copy["_v"] == hr_copy["_v"] == 1

    r = _save(client, hr, "tasks", {**hr_copy, "title": "Changed by HR"})
    assert r.status_code == 200, r.text
    assert r.json()["_v"] == 2

    r = _save(client, auth_headers, "tasks", {**admin_copy, "title": "Changed by admin"})
    assert r.status_code == 409, r.text
    assert r.headers.get("X-Edit-Conflict") == "1"
    assert f"Viewer {tag} changed this record" in r.json()["detail"]

    row, payload = _stored(db, task_id)
    assert payload["title"] == "Changed by HR"  # the earlier change is kept
    assert "_v" not in payload  # the version is never stored inside the record
    assert row.row_version == 2

    # After reloading, the admin's save goes through.
    fresh = _task(client, auth_headers, task_id)
    assert fresh["_v"] == 2 and fresh["title"] == "Changed by HR"
    r = _save(client, auth_headers, "tasks", {**fresh, "title": "Changed by admin"})
    assert r.status_code == 200, r.text
    assert _stored(db, task_id)[1]["title"] == "Changed by admin"


def test_own_repeat_save_and_save_without_version_go_through(client, db, auth_headers):
    _tag, hr, task_id = _setup(client, db, auth_headers)
    copy = _task(client, auth_headers, task_id)
    # The same person saving again from a copy that wasn't refreshed is not a conflict.
    assert _save(client, auth_headers, "tasks", {**copy, "title": "First"}).status_code == 200
    assert _save(client, auth_headers, "tasks", {**copy, "title": "Second"}).status_code == 200
    assert _stored(db, task_id)[1]["title"] == "Second"
    # A screen that rebuilds the record (no version) keeps last-write-wins, as before.
    hr_copy = _task(client, hr, task_id)
    assert _save(client, hr, "tasks", {**hr_copy, "title": "HR"}).status_code == 200
    record = {k: v for k, v in copy.items() if k != "_v"}
    assert _save(client, auth_headers, "tasks", {**record, "title": "No version"}).status_code == 200
    assert _stored(db, task_id)[1]["title"] == "No version"


def test_change_made_in_ess_protects_against_a_stale_hrms_save(client, db, auth_headers):
    tag, hr, _ = _setup(client, db, auth_headers)
    # A task assigned to the HR login's own employee, which they complete from ESS.
    me = client.get("/api/v1/ess/me", headers=hr).json()
    task_id = f"EC-ESS-{tag}"
    assert _save(client, auth_headers, "tasks", {"id": task_id, "title": "Count stock", "assigned_to": me["id"], "status": "todo"}).status_code == 200
    admin_copy = _task(client, auth_headers, task_id)

    r = client.patch(f"/api/v1/ess/tasks/{task_id}", headers=hr, json={"status": "done"})
    assert r.status_code == 200, r.text
    row, payload = _stored(db, task_id)
    assert payload["status"] == "done" and row.row_version == 2 and row.updated_by == f"emp:{me['id']}"

    r = _save(client, auth_headers, "tasks", {**admin_copy, "title": "Count stock (both shelves)"})
    assert r.status_code == 409, r.text
    assert _stored(db, task_id)[1]["status"] == "done"  # the employee's "done" is not undone


def test_bulk_save_never_stores_the_version(client, db, auth_headers):
    _tag, _hr, task_id = _setup(client, db, auth_headers)
    copy = _task(client, auth_headers, task_id)
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "bulk-save"},
                    json={"collection": "tasks", "records": [{**copy, "title": "Bulk"}]})
    assert r.status_code == 200, r.text
    row, payload = _stored(db, task_id)
    assert payload["title"] == "Bulk" and "_v" not in payload and row.row_version == 2
