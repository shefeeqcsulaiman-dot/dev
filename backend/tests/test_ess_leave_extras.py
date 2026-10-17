"""ESS additions (2026-10-17): a sick note attached to Sick Leave (required in ESS, viewable
in HRMS), the leave day count preview, department tasks, and holiday messages."""
import base64
import uuid
from datetime import date, timedelta

import pytest

from tests.test_ess_team_today_and_holidays import _company_id, _ess_login

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nnote").decode()


@pytest.fixture(autouse=True)
def _full_year_entitlement(monkeypatch):
    from app.routers import leave as _leave
    monkeypatch.setattr(_leave, "accrued_days", lambda settings, yearly, join_date, on: yearly)


def _monday_ahead(weeks=3):
    d = date.today() + timedelta(weeks=weeks)
    return d - timedelta(days=d.weekday())


def test_sick_leave_in_ess_needs_a_note_and_hr_can_open_it(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:5]
    _, ess = _ess_login(client, db, auth_headers, cid, f"SN{tag}", f"sn.{tag}", department="NoteDept")
    start = _monday_ahead()
    body = {"leave_type": "Sick Leave", "start_date": start.isoformat(), "end_date": start.isoformat()}

    r = client.post("/api/v1/ess/leave", headers=ess, json=body)
    assert r.status_code == 400 and "sick note" in r.json()["detail"]

    r = client.post("/api/v1/ess/leave", headers=ess, json={**body, "attachment": "data:text/html;base64,PGI+", "attachment_name": "x.html"})
    assert r.status_code == 400

    r = client.post("/api/v1/ess/leave", headers=ess, json={**body, "attachment": PNG, "attachment_name": "note.png"})
    assert r.status_code == 201, r.text
    leave_id = r.json()["id"]
    mine = client.get("/api/v1/ess/leave", headers=ess).json()
    assert next(l for l in mine if l["id"] == leave_id)["attachment_name"] == "note.png"

    own = client.get(f"/api/v1/ess/leave/{leave_id}/attachment", headers=ess)
    assert own.status_code == 200 and own.content.startswith(b"\x89PNG")
    hr = client.get(f"/api/v1/leave/requests/{leave_id}/attachment", headers=auth_headers)
    assert hr.status_code == 200 and hr.headers["content-type"] == "image/png"
    listed = client.get("/api/v1/leave/requests", headers=auth_headers).json()
    assert next(l for l in listed if l["id"] == leave_id)["attachment_name"] == "note.png"

    # Annual Leave needs no attachment
    nxt = start + timedelta(days=7)
    r = client.post("/api/v1/ess/leave", headers=ess, json={"leave_type": "Annual Leave", "start_date": nxt.isoformat(), "end_date": nxt.isoformat()})
    assert r.status_code == 201, r.text


def test_leave_day_preview_matches_the_request(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:5]
    _, ess = _ess_login(client, db, auth_headers, cid, f"DP{tag}", f"dp.{tag}", department="PrevDept")
    start = _monday_ahead(5)
    end = start + timedelta(days=6)
    d = client.get("/api/v1/ess/leave/days", headers=ess,
                   params={"leave_type": "Annual Leave", "start": start.isoformat(), "end": end.isoformat()}).json()
    assert d["calendar_days"] == 7 and d["days"] in range(1, 8)
    r = client.post("/api/v1/ess/leave", headers=ess, json={"leave_type": "Annual Leave", "start_date": start.isoformat(), "end_date": end.isoformat()})
    assert r.status_code == 201 and r.json()["days"] == d["days"]


def test_department_tasks_in_ess(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:5]
    dept = f"TaskDept{tag}"
    me, ess = _ess_login(client, db, auth_headers, cid, f"TK{tag}", f"tk.{tag}", department=dept)
    other, _ = _ess_login(client, db, auth_headers, cid, f"TO{tag}", f"to.{tag}", department=dept)
    for i, (who, title, status, due) in enumerate([(me, "Mine", "todo", "2026-12-01"), (other, "Colleague's", "in_progress", "2026-12-05"),
                                                   (other, "Finished", "done", "2026-11-01")]):
        r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "tasks", "record": {
            "id": f"T{tag}{i}", "title": title, "department": dept, "assigned_to": who.id, "assigned_to_name": who.full_name,
            "status": status, "due_date": due}})
        assert r.status_code == 200, r.text
    client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "tasks", "record": {
        "id": f"T{tag}x", "title": "Other dept", "department": "Elsewhere", "status": "todo"}})
    rows = client.get("/api/v1/ess/tasks/department", headers=ess).json()
    assert [t["title"] for t in rows] == ["Mine", "Colleague's", "Finished"]   # open first, done last
    assert rows[0]["is_mine"] is True and rows[1]["is_mine"] is False


def test_holiday_message_reaches_ess(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:5]
    _, ess = _ess_login(client, db, auth_headers, cid, f"HM{tag}", f"hm.{tag}")
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={"collection": "hrHolidays", "record": {
        "id": f"hm-{tag}", "date": "2031-12-02", "name": "National Day", "message": "Office closed; warehouse on call."}})
    assert r.status_code == 200
    rows = client.get("/api/v1/ess/holidays", headers=ess).json()
    assert next(h for h in rows if h["id"] == f"hm-{tag}")["message"] == "Office closed; warehouse on call."
