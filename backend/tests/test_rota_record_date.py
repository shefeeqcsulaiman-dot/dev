"""Rota reads filter by date in SQL (AppDataRecord.record_date) instead of parsing the whole
rota history; rows saved before the column existed are still found and get stamped once."""
import uuid
from datetime import date

from sqlalchemy import text

from app.main import ensure_schema_updates
from app.models import AppDataRecord
from app.rota_days import rota_day_statuses
from tests.test_attendance_monthly_report import _company_id


def _save_shift(client, h, emp_no, day, code="OFF"):
    rec = {"id": f"{emp_no}-{day}", "employee_id": emp_no, "employee_name": "R", "date": day, "type": "Off" if code == "OFF" else "Morning",
           "code": code, "mark": "Off" if code == "OFF" else "Shift", "status": "Published"}
    r = client.post("/api/v1/app-data?action=save", headers=h, json={"collection": "rotaAssignments", "record": rec})
    assert r.status_code == 200, r.text


def _row(db, emp_no, day):
    db.expire_all()
    return db.query(AppDataRecord).filter(AppDataRecord.collection == "rotaAssignments", AppDataRecord.record_key == f"{emp_no}-{day}").one()


def test_saved_rota_rows_get_their_date_and_range_reads_only_that_window(client, db, auth_headers):
    emp_no = f"RD-{uuid.uuid4().hex[:6]}"
    _save_shift(client, auth_headers, emp_no, "2031-03-03")
    _save_shift(client, auth_headers, emp_no, "2031-05-05")
    assert _row(db, emp_no, "2031-03-03").record_date == "2031-03-03"

    r = client.get("/api/v1/app-data/records/rotaAssignments/range?from=2031-03-01&to=2031-03-31", headers=auth_headers)
    days = {x["date"] for x in r.json()["records"] if x.get("employee_id") == emp_no}
    assert days == {"2031-03-03"}


def test_unstamped_rows_are_still_found_and_stamped_once_at_startup(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp_no = f"RD-{uuid.uuid4().hex[:6]}"
    _save_shift(client, auth_headers, emp_no, "2031-04-07")  # a Monday, marked Off
    row = _row(db, emp_no, "2031-04-07")
    db.execute(text("UPDATE app_data_records SET record_date = NULL WHERE id = :i"), {"i": row.id})
    db.commit()

    # an old row without record_date still counts
    assert rota_day_statuses(db, cid, [emp_no], date(2031, 4, 7), date(2031, 4, 7)) == {(emp_no, "2031-04-07"): "off"}
    r = client.get("/api/v1/app-data/records/rotaAssignments/range?from=2031-04-01&to=2031-04-30", headers=auth_headers)
    assert any(x.get("employee_id") == emp_no for x in r.json()["records"])

    db.execute(text("DELETE FROM schema_flags WHERE name = 'app_data_record_date_v1'"))
    db.commit()
    ensure_schema_updates()
    assert _row(db, emp_no, "2031-04-07").record_date == "2031-04-07"
