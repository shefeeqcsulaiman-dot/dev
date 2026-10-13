"""rota_records is the main copy of rota assignments (step C): every write stores the JSON
the row regenerates; text keeps its original value, the date and break are normalised."""
import json
import uuid

from app import rota_records
from app.models import AppDataRecord, RotaRecord
from app.rota_records import canonical_rota
from tests.test_hrms_department_scope import _company_id, _save


def test_round_trip_keeps_every_field():
    doc = {"id": "E1-2026-10-14", "employee_id": "E1", "employee_name": "Ali", "role": "Cashier", "date": "2026-10-14T00:00:00Z",
           "type": "Morning", "code": "m", "start": "09:00", "end": "17:00", "mark": "", "className": "morning",
           "break_minutes": "45", "notes": "", "tasks": [{"task_id": "T-1", "title": "Count"}], "status": "Draft"}
    out = canonical_rota(doc)
    assert out == {"employee_id": "E1", "employee_name": "Ali", "date": "2026-10-14", "code": "m", "type": "Morning",
                   "start": "09:00", "end": "17:00", "break_minutes": 45, "status": "Draft", "id": "E1-2026-10-14",
                   "role": "Cashier", "mark": "", "className": "morning", "notes": "",
                   "tasks": [{"task_id": "T-1", "title": "Count"}]}
    assert canonical_rota(out) == out
    assert canonical_rota({"employee_id": "E1", "date": "2026-10-14", "breakMinutes": "x"})["date"] == "2026-10-14"


def test_saves_store_what_the_row_regenerates(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp = f"RM-{uuid.uuid4().hex[:6]}"
    key = f"{emp}-2026-10-15"
    assert _save(client, auth_headers, "rotaAssignments", {
        "id": key, "employee_id": emp, "date": "2026-10-15", "code": "e", "type": "Evening", "start": "14:00",
        "end": "22:00", "break_minutes": 30, "tasks": [], "notes": "cover"}).status_code == 200
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.collection == "rotaAssignments", AppDataRecord.record_key == key).one()
    row = db.get(RotaRecord, rec.id)
    assert (row.code, row.kind, row.day, row.break_minutes) == ("E", "shift", "2026-10-15", 30)
    assert json.loads(row.extra) == {"id": key, "tasks": [], "notes": "cover", "code": "e"}  # as given; column upper case
    assert json.loads(rec.payload) == canonical_rota(json.loads(rec.payload))
    assert rota_records.verify_company(db, cid) and rota_records.stale_json(db, cid) == []
