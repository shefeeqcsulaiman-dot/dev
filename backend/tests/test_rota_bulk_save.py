"""The rota buttons (Auto Fill, Copy Previous Week, Repeat, Publish) save shifts in batches
of 200 through bulk-save instead of one request per shift. A batch must land exactly like
single saves: readable through the week/month range endpoint, with a re-save updating in place."""
import time
import uuid
from datetime import date, timedelta

from app.models import AppDataRecord, RotaRecord
from tests.test_ess_requests import _company_id


def _week(start, staff):
    rows = []
    for s in staff:
        for i in range(7):
            day = (start + timedelta(days=i)).isoformat()
            rows.append({"id": f"{s}-{day}", "employee_id": s, "employee_name": f"Staff {s}", "date": day,
                         "type": "Off" if i == 6 else "Morning", "code": "OFF" if i == 6 else "M",
                         "mark": "Off" if i == 6 else "Shift", "status": "Draft", "department": "Veterinary"})
    return rows


def _bulk(client, headers, records):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "bulk-save"},
                       json={"collection": "rotaAssignments", "records": records})


def test_a_full_week_for_68_staff_saves_in_three_batches(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:4]
    staff = [f"R{tag}{n}" for n in range(1, 69)]
    start = date(2032, 3, 1)
    rows = _week(start, staff)
    assert len(rows) == 476

    began = time.perf_counter()
    for i in range(0, len(rows), 200):
        r = _bulk(client, auth_headers, rows[i:i + 200])
        assert r.status_code == 200, r.text
    elapsed = time.perf_counter() - began
    assert elapsed < 30, f"476 shifts took {elapsed:.1f}s"

    got = client.get("/api/v1/app-data/records/rotaAssignments/range", headers=auth_headers,
                     params={"from": "2032-03-01", "to": "2032-03-07"}).json()["records"]
    mine = [g for g in got if g["employee_id"] in set(staff)]
    assert len(mine) == 476
    assert db.query(RotaRecord).filter(RotaRecord.company_id == cid, RotaRecord.employee_no.in_(staff)).count() == 476

    # publishing the week re-saves the same shifts: updated in place, no duplicates
    for row in rows:
        row["status"] = "Published"
    for i in range(0, len(rows), 200):
        body = _bulk(client, auth_headers, rows[i:i + 200]).json()
        assert body["updated"] == len(rows[i:i + 200]) and body["created"] == 0
    count = db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "rotaAssignments",
                                           AppDataRecord.record_key.in_([r["id"] for r in rows])).count()
    assert count == 476
    again = client.get("/api/v1/app-data/records/rotaAssignments/range", headers=auth_headers,
                       params={"from": "2032-03-01", "to": "2032-03-07"}).json()["records"]
    assert {g["status"] for g in again if g["employee_id"] in set(staff)} == {"Published"}
