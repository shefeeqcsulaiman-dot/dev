"""task_records is the main copy of tasks (step C): every write, ESS included, stores the
JSON the row regenerates; text keeps its original value, numbers are normalised."""
import json
import uuid

from app import task_records
from app.models import AppDataRecord, TaskRecord
from app.task_records import canonical_task
from tests.test_hrms_department_scope import _company_id, _login_as, _save


def test_round_trip_normalises_numbers_and_keeps_text_exactly():
    doc = {"id": "T-1", "title": " Count stock ", "assigned_to": 42, "progress": "30.7", "due_date": "2026-10-20T08:00:00Z",
           "status": "todo", "checklist": [{"item": "shelf A", "done": False}], "color": "#f00"}
    out = canonical_task(doc)
    assert out == {"title": " Count stock ", "assigned_to": 42, "status": "todo", "progress": 30,
                   "due_date": "2026-10-20", "id": "T-1", "checklist": [{"item": "shelf A", "done": False}], "color": "#f00"}
    assert canonical_task(out) == out
    assert canonical_task({"title": "X", "progress": "n/a", "due_date": "soon"}) == {"title": "X", "due_date": ""}


def test_every_write_path_stores_the_regenerated_json(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:8]
    task_id = f"MC-{tag}"
    assert _save(client, auth_headers, "tasks", {"id": task_id, "title": "Main copy", "status": "todo", "progress": "10",
                                                 "notes": "keep me"}).status_code == 200

    def stored():
        db.expire_all()
        rec = db.query(AppDataRecord).filter(AppDataRecord.collection == "tasks", AppDataRecord.record_key == task_id).one()
        return rec, json.loads(rec.payload)

    rec, doc = stored()
    assert (doc["progress"], doc["notes"]) == (10, "keep me")
    assert json.loads(db.get(TaskRecord, rec.id).extra) == {"id": task_id, "notes": "keep me"}

    # An ESS status change writes the row's JSON directly: it is regenerated too.
    hr = _login_as(client, db, auth_headers, cid, tag, [], ["hr_workflow:view", "hr_workflow:edit"])
    me = client.get("/api/v1/ess/me", headers=hr).json()
    assert _save(client, auth_headers, "tasks", {"id": task_id, "title": "Main copy", "assigned_to": me["id"],
                                                 "status": "todo", "progress": "10"}).status_code == 200
    assert client.patch(f"/api/v1/ess/tasks/{task_id}", headers=hr, json={"status": "done"}).status_code == 200
    rec, doc = stored()
    assert (doc["status"], doc["progress"]) == ("done", 100) and doc == canonical_task(doc)
    assert task_records.stale_json(db, cid) == [] and task_records.verify_company(db, cid)
