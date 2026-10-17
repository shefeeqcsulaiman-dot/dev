"""HR Settings > Leave Policy > Leave Types > Status: an Inactive type can't be requested
(HRMS or ESS) and drops out of the ESS balance; switching it back to Active restores both."""
from datetime import date, timedelta

from tests.test_ess_requests import _company_id, _ess_login


def _set_policy(client, headers, inactive):
    types = [{"type": t, "days": d, "status": "Inactive" if t in inactive else "Active"}
             for t, d in (("Annual Leave", "21"), ("Sick Leave", "90"), ("Hajj Leave", "30"))]
    r = client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                    json={"collection": "hrLeavePolicy", "record": {"id": "leave-policy", "leave_types": types}})
    assert r.status_code == 200, r.text


def test_inactive_type_is_refused_and_hidden(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, h = _ess_login(client, db, auth_headers, cid, "LTS-1", "lts.one")
    day = (date.today() + timedelta(days=20)).isoformat()
    try:
        _set_policy(client, auth_headers, {"Hajj Leave"})

        r = client.post("/api/v1/ess/leave", headers=h, json={"leave_type": "Hajj Leave", "start_date": day, "end_date": day})
        assert r.status_code == 400 and "switched off" in r.text, r.text
        r = client.post("/api/v1/leave/requests", headers=auth_headers,
                        json={"employee_id": emp.id, "leave_type": "Hajj Leave", "start_date": day, "end_date": day})
        assert r.status_code == 400 and "switched off" in r.text, r.text

        bal = client.get("/api/v1/ess/leave-balance", headers=h).json()
        assert "Hajj Leave" not in bal["by_type"] and bal["inactive_types"] == ["Hajj Leave"]
        assert "Annual Leave" in bal["by_type"]

        # other types are unaffected
        r = client.post("/api/v1/ess/leave", headers=h, json={"leave_type": "Sick Leave", "start_date": day, "end_date": day, "attachment": "data:image/png;base64,iVBORw0KGgo="})
        assert r.status_code == 201, r.text

        _set_policy(client, auth_headers, set())
        later = (date.today() + timedelta(days=40)).isoformat()
        r = client.post("/api/v1/ess/leave", headers=h, json={"leave_type": "Hajj Leave", "start_date": later, "end_date": later})
        assert r.status_code == 201, r.text
        assert "Hajj Leave" in client.get("/api/v1/ess/leave-balance", headers=h).json()["by_type"]
    finally:
        # The test company is shared: remove the policy record again, as other tests seed their own.
        client.post("/api/v1/app-data", headers=auth_headers, params={"action": "delete"},
                    json={"collection": "hrLeavePolicy", "record": {"id": "leave-policy"}})
