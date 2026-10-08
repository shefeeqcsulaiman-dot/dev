"""GET /app-data/export: written row by row to a temp file and streamed (memory stays
flat), same JSON shape as before -- app-data collections oldest first, the real tables
under "<table>_db", then audit and users."""
import json

from app.models import AppDataRecord
from tests.conftest import ensure_user


def _headers(client, db, email, trn):
    user = ensure_user(db, email, trn)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"})
    assert r.status_code == 200, r.text
    return user.company_id, {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_export_shape_and_order(client, db):
    cid, headers = _headers(client, db, "export-shape@taxflowqa.com", "900000000000741")
    other_cid, _ = _headers(client, db, "export-other@taxflowqa.com", "900000000000742")
    import datetime as dt
    base = dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc)
    for i in (2, 0, 1):
        db.add(AppDataRecord(company_id=cid, collection="salesInvoices", record_key=f"EXP-{i}",
                             payload=json.dumps({"invoice_no": f"EXP-{i}", "customer": "Ünïcode Co"}),
                             created_at=base + dt.timedelta(days=i)))
    db.add(AppDataRecord(company_id=cid, collection="tasks", record_key="T-1", payload=json.dumps({"title": "t"})))
    # An app-data collection named like a fixed key never overrode it, and still doesn't.
    db.add(AppDataRecord(company_id=cid, collection="users", record_key="U-X", payload=json.dumps({"bogus": True})))
    db.add(AppDataRecord(company_id=other_cid, collection="salesInvoices", record_key="OTHER-1",
                         payload=json.dumps({"invoice_no": "OTHER-1"})))
    db.commit()

    r = client.get("/api/v1/app-data/export", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["ok"] is True and body["meta"]["company_id"] == cid
    data = body["data"]
    numbers = [rec["invoice_no"] for rec in data["salesInvoices"]]
    assert numbers == ["EXP-0", "EXP-1", "EXP-2"]            # oldest first, this company only
    assert data["salesInvoices"][0]["customer"] == "Ünïcode Co"
    assert [t["title"] for t in data["tasks"]] == ["t"]
    assert [u["email"] for u in data["users"]] == ["export-shape@taxflowqa.com"]
    assert data["accounts_db"] and all("code" in a for a in data["accounts_db"])
    for key in ("invoices_db", "invoice_lines_db", "source_transactions_db", "general_ledger_db", "employees_db",
                "payroll_runs_db", "payroll_items_db", "leave_requests_db", "attendance_details_db", "audit"):
        assert isinstance(data[key], list), key


def test_export_with_no_app_data_is_valid_json(client, db):
    _, headers = _headers(client, db, "export-empty@taxflowqa.com", "900000000000743")
    r = client.get("/api/v1/app-data/export", headers=headers)
    assert r.status_code == 200
    assert r.json()["data"]["users"]
