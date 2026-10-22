"""GET /ess/start (2026-10-22): ESS's first load in one request. Each answer must be exactly
what the app would get from that path on its own, and a bad token is refused once."""
import json
from datetime import date

from tests.test_ess_requests import _company_id, _ess_login


def test_ess_start_matches_each_endpoint(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _emp, h = _ess_login(client, db, auth_headers, cid, "ESS-START-1", "ess.start1")
    month = date.today().strftime("%Y-%m")
    r = client.get(f"/api/v1/ess/start?month={month}", headers=h)
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert f"/ess/rota?month={month}" in results and "/ess/me" in results and len(results) == 18
    for path, answer in results.items():
        alone = client.get("/api/v1" + path, headers=h)
        assert answer["status"] == alone.status_code, path
        assert json.dumps(answer["body"], sort_keys=True) == json.dumps(alone.json(), sort_keys=True), path


def test_ess_start_needs_a_valid_token(client):
    assert client.get("/api/v1/ess/start", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/v1/ess/start?month=2026-1", headers={"Authorization": "Bearer nope"}).status_code in (401, 422)
