"""lookup_records is the main copy of the small lookup lists: one row per entry, the JSON
left exactly as saved."""
import json
import uuid

from app import lookup_records
from app.lookup_records import canonical_lookup
from app.models import AppDataRecord, LookupRecord
from tests.test_hrms_department_scope import _company_id, _save


def test_rows_for_each_list_and_json_unchanged(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    entries = (("salesUnits", {"id": f"U-{tag}", "name": f"Box {tag}", "code": "BOX"}),
               ("rotaShifts", {"code": f"N{tag}", "name": "Night", "start": "22:00", "end": "06:00", "break_minutes": 60}),
               ("bankAccounts", {"id": f"BA-{tag}", "name": "ENBD Current", "account_no": "1234", "status": "Active"}))
    for collection, record in entries:
        assert _save(client, auth_headers, collection, record).status_code == 200, collection
        assert canonical_lookup(record) == record
    db.expire_all()
    night = db.query(LookupRecord).filter(LookupRecord.company_id == cid, LookupRecord.code == f"N{tag}").one()
    assert (night.collection, night.name, json.loads(night.extra)) == ("rotaShifts", "Night",
                                                                       {"start": "22:00", "end": "06:00", "break_minutes": 60})
    stored = json.loads(db.query(AppDataRecord.payload).filter(AppDataRecord.id == night.id).scalar())
    assert stored == entries[1][1]
    assert lookup_records.verify_company(db, cid)
