"""GET /reports/branch-performance must not leak other branches' revenue/
profit to a Branch Login session (or a branch-locked Employee) — it was
returning the full company-wide breakdown to every caller regardless of
principal kind, discovered live: a Branch Login could see every other
branch's numbers on its own Dashboard's Branch Performance card."""
import json

from app.models import AppDataRecord


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _create_branch(client, admin_headers, name):
    r = client.post("/api/v1/branches", headers=admin_headers, json={
        "name": name, "username": name.lower().replace(" ", "-") + "-login", "password": "branchlogin123",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _branch_login(client, username, password="branchlogin123"):
    r = client.post("/api/v1/branches/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_invoice(db, company_id, branch_id, amount):
    db.add(AppDataRecord(
        company_id=company_id, branch_id=branch_id, collection="salesInvoices",
        record_key=f"seed-{branch_id}-{amount}",
        payload=json.dumps({"status": "paid", "subtotal": amount, "total": amount}),
    ))
    db.commit()


def test_branch_login_only_sees_its_own_branch(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    branch_a = _create_branch(client, auth_headers, "Perf Isolation Branch A")
    branch_b = _create_branch(client, auth_headers, "Perf Isolation Branch B")
    _seed_invoice(db, company_id, branch_a["id"], 1000)
    _seed_invoice(db, company_id, branch_b["id"], 2000)

    admin_resp = client.get("/api/v1/reports/branch-performance", headers=auth_headers)
    assert admin_resp.status_code == 200, admin_resp.text
    admin_ids = {row["branch_id"] for row in admin_resp.json()["branches"]}
    assert branch_a["id"] in admin_ids and branch_b["id"] in admin_ids

    branch_a_headers = _branch_login(client, branch_a["username"])
    scoped_resp = client.get("/api/v1/reports/branch-performance", headers=branch_a_headers)
    assert scoped_resp.status_code == 200, scoped_resp.text
    scoped_data = scoped_resp.json()
    scoped_ids = {row["branch_id"] for row in scoped_data["branches"]}
    assert scoped_ids == {branch_a["id"]}
    assert branch_b["id"] not in scoped_ids
    assert scoped_data["unassigned"] is None


def test_branch_with_zero_activity_still_listed(client, auth_headers):
    """A brand-new branch with no invoices/purchases/bills yet must still
    appear in `branches` (with zero figures) — previously it was silently
    dropped because `bucket()` was only ever populated from actual
    transaction rows, so head office had no "View as" row to click for a
    branch until it had at least one transaction."""
    quiet_branch = _create_branch(client, auth_headers, "Perf Isolation Quiet Branch")

    resp = client.get("/api/v1/reports/branch-performance", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["has_branches"] is True
    row = next((r for r in data["branches"] if r["branch_id"] == quiet_branch["id"]), None)
    assert row is not None, "zero-activity branch missing from branch-performance response"
    assert row["revenue"] == "0.00"
    assert row["profit"] == "0.00"
