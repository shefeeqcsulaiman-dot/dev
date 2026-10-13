"""Step D: the lookup lists' JSON is no longer stored -- it is rebuilt from lookup_records on
read (app/record_payloads.py). Every way of reading, editing, exporting and backing up a
record must still see the whole record."""
import json
import uuid

from sqlalchemy import text

from app.models import AppDataRecord, LookupRecord
from app.rota_days import rota_break_seconds
from tests.test_hrms_department_scope import _company_id, _save


def _raw_payload(db, record_id):
    return db.execute(text("SELECT payload FROM app_data_records WHERE id = :i"), {"i": record_id}).scalar()


def test_json_is_rebuilt_from_the_table_everywhere(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    unit = {"id": f"U-{tag}", "name": f"Carton {tag}", "code": f"CTN{tag}", "notes": "outer pack"}
    assert _save(client, auth_headers, "salesUnits", unit).status_code == 200
    db.expire_all()
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.record_key == unit["code"]).one()
    assert _raw_payload(db, rec.id) == ""                                       # not stored
    assert json.loads(rec.payload) == unit                                      # rebuilt on read
    assert db.get(LookupRecord, rec.id).name == unit["name"]

    # Bootstrap, the paged list and the JSON export all carry the whole record.
    boot = client.get("/api/v1/app-data", headers=auth_headers).json()
    assert unit in [{k: v for k, v in u.items() if k != "_v"} for u in (boot.get("data", boot)["salesUnits"])]
    listed = client.get("/api/v1/app-data/records/salesUnits", headers=auth_headers, params={"limit": 500}).json()["records"]
    assert any(r.get("code") == unit["code"] and r.get("notes") == "outer pack" for r in listed)
    export = client.get("/api/v1/app-data/export", headers=auth_headers)
    assert export.status_code == 200 and '"notes":"outer pack"' in export.text   # compact JSON

    # Editing it keeps the other fields; the edit is stored in the table, not as JSON.
    edited = {**unit, "name": f"Big carton {tag}"}
    assert _save(client, auth_headers, "salesUnits", edited).status_code == 200
    db.expire_all()
    rec = db.get(AppDataRecord, rec.id)
    assert _raw_payload(db, rec.id) == "" and json.loads(rec.payload) == edited

    # Delete removes record and row.
    record_id = rec.id
    r = client.post("/api/v1/app-data?action=delete", headers=auth_headers, json={"collection": "salesUnits", "record": edited})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(AppDataRecord, record_id) is None and db.get(LookupRecord, record_id) is None


def test_server_readers_of_a_lookup_list_see_its_fields(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    assert _save(client, auth_headers, "rotaShifts", {"code": f"Z{tag}", "name": "Zone", "start": "08:00", "end": "16:00",
                                                      "break_minutes": 45}).status_code == 200
    emp = f"RP-{tag}"
    assert _save(client, auth_headers, "rotaAssignments", {"id": f"{emp}-2026-10-20", "employee_id": emp, "date": "2026-10-20",
                                                           "code": f"Z{tag}"}).status_code == 200
    from datetime import date
    db.expire_all()
    # The shift's own break (stored only in the table now) is what the rota uses.
    assert rota_break_seconds(db, cid, [emp], date(2026, 10, 20), date(2026, 10, 20)) == {(emp, "2026-10-20"): 45 * 60}
