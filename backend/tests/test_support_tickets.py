"""Support tickets (2026-10-21): a company user raises a ticket, Super Admin answers in it,
each side sees an unread mark for the other's replies, and companies only see their own."""
import base64

from tests.test_superadmin_delete_company import _login_client_ref, _make_superadmin

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nshot").decode()


def test_ticket_conversation_between_company_and_super_admin(client, db, auth_headers, second_tenant_headers):
    _login_client_ref["client"] = client
    sa = _make_superadmin(db)
    r = client.post("/api/v1/support/tickets", headers=auth_headers, json={
        "subject": "Invoice PDF shows wrong VAT", "category": "Bug", "priority": "High",
        "message": "The PDF says 0% VAT.", "attachment": PNG, "attachment_name": "shot.png"})
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["reference"].startswith("T-") and t["status"] == "open" and t["unread"] is False

    # Super Admin: new ticket is unread, with the company name and the screenshot
    assert client.get("/api/v1/superadmin/support/unread", headers=sa).json()["unread"] >= 1
    listed = client.get("/api/v1/superadmin/support/tickets?status=active", headers=sa).json()
    row = next(x for x in listed if x["id"] == t["id"])
    assert row["unread"] is True and row["company_name"] and row["priority"] == "High"
    full = client.get(f"/api/v1/superadmin/support/tickets/{t['id']}", headers=sa).json()
    assert full["messages"][0]["has_attachment"] is True and full["unread"] is False   # opening clears it
    shot = client.get(f"/api/v1/superadmin/support/messages/{full['messages'][0]['id']}/attachment", headers=sa)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/png"

    # Reply: in progress, unread for the company
    r = client.post(f"/api/v1/superadmin/support/tickets/{t['id']}/messages", headers=sa, json={"message": "Fixed, please check."})
    assert r.status_code == 201
    assert client.get("/api/v1/support/unread", headers=auth_headers).json()["unread"] >= 1
    mine = client.get(f"/api/v1/support/tickets/{t['id']}", headers=auth_headers).json()
    assert mine["status"] == "in_progress" and [m["side"] for m in mine["messages"]] == ["company", "support"]
    assert next(x for x in client.get("/api/v1/support/tickets", headers=auth_headers).json() if x["id"] == t["id"])["unread"] is False

    # Resolved, then the company writes again: open again and unread for support
    assert client.post(f"/api/v1/superadmin/support/tickets/{t['id']}/status", headers=sa, json={"status": "resolved"}).json()["status"] == "resolved"
    client.post(f"/api/v1/support/tickets/{t['id']}/messages", headers=auth_headers, json={"message": "Still wrong on credit notes."})
    again = client.get(f"/api/v1/superadmin/support/tickets?q=Invoice PDF", headers=sa).json()
    row = next(x for x in again if x["id"] == t["id"])
    assert row["status"] == "open" and row["unread"] is True

    # Another company sees nothing of it
    assert client.get(f"/api/v1/support/tickets/{t['id']}", headers=second_tenant_headers).status_code == 404
    assert all(x["id"] != t["id"] for x in client.get("/api/v1/support/tickets", headers=second_tenant_headers).json())


def test_ticket_input_and_who_can_raise(client, db, auth_headers):
    _login_client_ref["client"] = client
    sa = _make_superadmin(db)
    base = {"subject": "Help", "message": "Hi"}
    assert client.post("/api/v1/support/tickets", headers=auth_headers, json={**base, "category": "Gossip"}).status_code == 422
    assert client.post("/api/v1/support/tickets", headers=auth_headers, json={**base, "subject": "  "}).status_code == 422
    assert client.post("/api/v1/support/tickets", headers=auth_headers, json={**base, "attachment": "data:text/html;base64,PGI+"}).status_code == 400
    # Super Admin answers tickets, it does not raise them
    assert client.post("/api/v1/support/tickets", headers=sa, json=base).status_code == 403
    assert client.get("/api/v1/superadmin/support/tickets", headers=auth_headers).status_code == 403
