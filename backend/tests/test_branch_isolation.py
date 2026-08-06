"""Branch Management: data isolation tests.

Phase 2: HR/Attendance — the first module scoped by branch_id, proving out
the principal.branch_id filter pattern.
Phase 3: Accounting (trial balance) — a purely ORM-derived report (posted
JournalLine/JournalEntry only, no AppDataRecord JSON dependency), so it can
be fully and correctly branch-filtered today. The Dashboard/Summary reports
mix in AppDataRecord-JSON-derived sales/purchases data, which has no
branch_id yet (that lands in a later phase alongside POS/Sales) — widening
those to accept branch-scoped principals now would silently under-filter
and leak company-wide data, so they're deliberately left User-only until
AppDataRecord itself is branch-aware.
Phase 5: Inventory/Purchases — /inventory/stock-levels and /inventory/
stock-movements (StockMovement.branch_id, stamped by Phase 4's write-path
widening) plus the purchaseRecords AppDataRecord collection specifically
(_BRANCH_FILTERED_COLLECTIONS in app_data.py — an explicit allowlist, not
every collection, since POS/Sales collections are still Phase 6's job).
Phase 6: POS/Sales, the last data-scoping phase — posSales and
salesInvoices added to _BRANCH_FILTERED_COLLECTIONS, plus GET/POST
/invoices (the real ORM Invoice table, branch_id since Phase 3) widened
and filtered the same way trial-balance was in Phase 3.
"""

from datetime import UTC, datetime
from decimal import Decimal

from app.models import AppDataRecord, AttendanceSession, Employee


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
    # auth_headers reuses the same tenant across tests in this session-scoped
    # test DB, so role NAMES (unique per company) can't be minted fresh per
    # call the way test_hrms_rbac_login.py does — but hr_dashboard() branches
    # on an EXACT role_name match ("Administrator"/"HR Manager"), so this
    # helper reuses an existing role of that name across tests instead of
    # suffixing it into uniqueness.
    existing = client.get("/api/v1/hr/admin/roles", headers=admin_headers).json()
    role = next((r for r in existing if r["role_name"] == role_name), None)
    if not role:
        r = client.post(
            "/api/v1/hr/admin/roles",
            headers=admin_headers,
            json={"role_name": role_name, "description": "test role", "permission_keys": permission_keys},
        )
        assert r.status_code == 201, r.text
        role = r.json()

    r = client.put(
        f"/api/v1/hr/admin/employees/{employee_id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": "branchtest123", "role_id": role["id"], "is_active": True},
    )
    assert r.status_code == 200, r.text

    r = client.post("/api/v1/ess/login", json={"username": username, "password": "branchtest123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _open_session(db, company_id, employee_id, branch_id):
    session = AttendanceSession(
        company_id=company_id, employee_id=employee_id, branch_id=branch_id,
        check_in=datetime.now(UTC), status="open",
    )
    db.add(session)
    db.commit()
    return session


def test_branch_scoped_administrator_only_sees_own_branch_attendance(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Dubai Marina"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Deira"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-A", full_name="Marina Admin", branch_id=branch_a["id"])
    emp_b = Employee(company_id=company_id, employee_no="BR-ISO-B", full_name="Deira Admin", branch_id=branch_b["id"])
    db.add_all([emp_a, emp_b])
    db.commit()

    # One open session per branch.
    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_b.id, branch_b["id"])

    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.marina", ["employees:view"], "Administrator")

    dash = client.get("/api/v1/hr/dashboard", headers=headers_a)
    assert dash.status_code == 200, dash.text
    # Branch A's Administrator sees only Branch A's open session, not Branch B's.
    assert dash.json()["active_sessions_now"] == 1


def test_branch_scoped_employee_still_sees_branch_less_legacy_sessions(client, db, auth_headers):
    """NULL branch_id means "predates Branch Management" — must stay
    visible to a branch-scoped viewer, not silently vanish."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Business Bay"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-C", full_name="Business Bay Admin", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()

    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.bizbay", ["employees:view"], "Administrator")
    # Delta, not absolute count — a NULL branch_id is visible to every
    # branch-scoped viewer, so other tests' legacy sessions on this shared
    # tenant could otherwise make an exact count flaky.
    before = client.get("/api/v1/hr/dashboard", headers=headers_a).json()["active_sessions_now"]

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_a.id, None)  # legacy, pre-branch session

    dash = client.get("/api/v1/hr/dashboard", headers=headers_a)
    assert dash.status_code == 200
    assert dash.json()["active_sessions_now"] == before + 2


def test_unassigned_employee_sees_all_branches_unchanged(client, db, auth_headers):
    """Backward compatibility: an Employee with no branch_id (the only
    possibility before this feature, and for companies that never set up
    branches) sees company-wide data exactly like today."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Sharjah"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Ajman"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_unassigned = Employee(company_id=company_id, employee_no="BR-ISO-D", full_name="HQ Admin")
    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-E", full_name="Sharjah Staff", branch_id=branch_a["id"])
    db.add_all([emp_unassigned, emp_a])
    db.commit()

    headers_unassigned = _grant_role_and_login(
        client, auth_headers, emp_unassigned.id, "branchtest.hq", ["employees:view"], "Administrator"
    )
    # This test tenant is shared across other tests in this file/session, so
    # assert a DELTA rather than an absolute count — robust regardless of
    # sessions other tests already left open on the same company.
    before = client.get("/api/v1/hr/dashboard", headers=headers_unassigned).json()["active_sessions_now"]

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_a.id, branch_b["id"])

    dash = client.get("/api/v1/hr/dashboard", headers=headers_unassigned)
    assert dash.status_code == 200
    assert dash.json()["active_sessions_now"] == before + 2


def test_live_locations_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Al Ain"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Fujairah"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    emp_a = Employee(company_id=company_id, employee_no="BR-ISO-F", full_name="Al Ain Viewer", branch_id=branch_a["id"])
    emp_b = Employee(company_id=company_id, employee_no="BR-ISO-G", full_name="Fujairah Staff", branch_id=branch_b["id"])
    db.add_all([emp_a, emp_b])
    db.commit()

    _open_session(db, company_id, emp_a.id, branch_a["id"])
    _open_session(db, company_id, emp_b.id, branch_b["id"])

    headers_a = _grant_role_and_login(
        client, auth_headers, emp_a.id, "branchtest.alain", ["hr:view_all_attendance"], "Attendance Viewer"
    )
    live = client.get("/api/v1/hr/live-locations", headers=headers_a)
    assert live.status_code == 200, live.text
    employee_ids = {row["employee_id"] for row in live.json()}
    # A NULL branch_id (legacy data, possibly left by other tests sharing
    # this tenant) is visible too, so assert membership, not an exact set.
    assert emp_a.id in employee_ids
    assert emp_b.id not in employee_ids


def _post_and_approve_source(client, headers, reference, branch_id, account_code="3000"):
    created = client.post(
        "/api/v1/source-transactions",
        headers=headers,
        json={
            "module": "sales",
            "reference": reference,
            "party_name": "Branch Trial Balance Test",
            "branch_id": branch_id,
            "lines": [{"description": "Sale", "account_code": account_code, "quantity": "1", "unit_price": "1000.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["branch_id"] == branch_id
    approved = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=headers)
    assert approved.status_code == 202, approved.text
    return source


def _trial_balance_account_row(client, headers, code):
    rows = client.get("/api/v1/reports/trial-balance", headers=headers).json()["rows"]
    return next((r for r in rows if r["code"] == code), None)


def test_trial_balance_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-TB-A", full_name="TB Branch A Admin", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.tba", ["employees:view"], "Administrator")

    # Baseline before either branch's postings, so the assertion is a delta
    # (shared tenant, other tests may have already posted to account 3000).
    before_a = _trial_balance_account_row(client, headers_a, "3000")
    before_credit_a = Decimal(before_a["credit"]) if before_a else Decimal("0")

    _post_and_approve_source(client, auth_headers, "SRC-TB-A-001", branch_a["id"])
    _post_and_approve_source(client, auth_headers, "SRC-TB-B-001", branch_b["id"])

    # Branch A's viewer sees only Branch A's 1000.00 sale on account 3000,
    # not Branch B's — a company-wide trial balance would show both.
    row_a = _trial_balance_account_row(client, headers_a, "3000")
    assert row_a is not None
    assert Decimal(row_a["credit"]) - before_credit_a == Decimal("1000.00")


def test_trial_balance_unassigned_employee_sees_all_branches(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch C"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch D"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_unassigned = Employee(company_id=company_id, employee_no="BR-TB-U", full_name="TB HQ Admin")
    db.add(emp_unassigned)
    db.commit()
    headers_u = _grant_role_and_login(client, auth_headers, emp_unassigned.id, "branchtest.tbu", ["employees:view"], "Administrator")

    before = _trial_balance_account_row(client, headers_u, "3000")
    before_credit = Decimal(before["credit"]) if before else Decimal("0")

    _post_and_approve_source(client, auth_headers, "SRC-TB-C-001", branch_a["id"])
    _post_and_approve_source(client, auth_headers, "SRC-TB-D-001", branch_b["id"])

    # Unassigned (no branch_id) employee sees company-wide totals, both
    # branches' 1000.00 sales included — unchanged from pre-feature behavior.
    row = _trial_balance_account_row(client, headers_u, "3000")
    assert Decimal(row["credit"]) - before_credit == Decimal("2000.00")


def test_trial_balance_requires_reports_view_permission(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "TB Branch E"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-TB-NOPERM", full_name="No Reports Access", branch_id=branch_a["id"])
    db.add(emp)
    db.commit()
    # Deliberately no "reports:view" in the granted permissions.
    headers_no_perm = _grant_role_and_login(
        client, auth_headers, emp.id, "branchtest.tbnoperm", ["employees:view"], "Branch Staff No Reports"
    )
    resp = client.get("/api/v1/reports/trial-balance", headers=headers_no_perm)
    assert resp.status_code == 403


def _save_purchase_record(client, headers, ref, branch_id, sku, quantity):
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref,
                "supplier": "Inventory Test Supplier",
                "branch_id": branch_id,
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "lines": [{"sku": sku, "product": sku, "quantity": quantity, "unit_cost": 10}],
            },
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_stock_levels_and_movements_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-INV-A", full_name="Inv Branch A Admin", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.inva", ["employees:view"], "Administrator")

    sku = "INV-ISO-SKU-1"
    _save_purchase_record(client, auth_headers, "PUR-INV-A-001", branch_a["id"], sku, 10)
    _save_purchase_record(client, auth_headers, "PUR-INV-B-001", branch_b["id"], sku, 7)

    levels = client.get("/api/v1/inventory/stock-levels", headers=headers_a)
    assert levels.status_code == 200, levels.text
    row = next((r for r in levels.json() if r["code"] == sku), None)
    assert row is not None
    # Branch A's viewer sees only Branch A's 10 units, not Branch B's 7.
    assert row["current_stock"] == 10

    movements = client.get("/api/v1/inventory/stock-movements", headers=headers_a)
    assert movements.status_code == 200, movements.text
    refs = {m["reference"] for m in movements.json()}
    assert "PUR-INV-A-001" in refs
    assert "PUR-INV-B-001" not in refs


def test_stock_levels_unassigned_employee_sees_all_branches(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch C"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch D"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_u = Employee(company_id=company_id, employee_no="BR-INV-U", full_name="Inv HQ Admin")
    db.add(emp_u)
    db.commit()
    headers_u = _grant_role_and_login(client, auth_headers, emp_u.id, "branchtest.invu", ["employees:view"], "Administrator")

    sku = "INV-ISO-SKU-2"
    _save_purchase_record(client, auth_headers, "PUR-INV-C-001", branch_a["id"], sku, 4)
    _save_purchase_record(client, auth_headers, "PUR-INV-D-001", branch_b["id"], sku, 6)

    levels = client.get("/api/v1/inventory/stock-levels", headers=headers_u)
    assert levels.status_code == 200
    row = next((r for r in levels.json() if r["code"] == sku), None)
    assert row is not None
    assert row["current_stock"] == 10


def test_purchase_records_collection_filtered_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch E"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Inv Branch F"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-INV-E", full_name="Inv Branch E Staff", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.inve", ["employees:view"], "Purchases Only Role")

    _save_purchase_record(client, auth_headers, "PUR-INV-E-001", branch_a["id"], "INV-ISO-SKU-3", 1)
    _save_purchase_record(client, auth_headers, "PUR-INV-F-001", branch_b["id"], "INV-ISO-SKU-4", 1)

    listed = client.get("/api/v1/app-data/records/purchaseRecords", headers=headers_a)
    assert listed.status_code == 200, listed.text
    refs = {rec["ref"] for rec in listed.json()["records"]}
    assert "PUR-INV-E-001" in refs
    assert "PUR-INV-F-001" not in refs

    # Admin (User token) is never branch-scoped — sees both.
    listed_admin = client.get("/api/v1/app-data/records/purchaseRecords", headers=auth_headers)
    admin_refs = {rec["ref"] for rec in listed_admin.json()["records"]}
    assert {"PUR-INV-E-001", "PUR-INV-F-001"}.issubset(admin_refs)


def _save_pos_sale(client, headers, ref, branch_id, total):
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={
            "collection": "posSales",
            "record": {
                "receipt_no": ref,
                "id": ref,
                "branch_id": branch_id,
                "customer": "Walk-in",
                "subtotal": total,
                "vat": 0,
                "total": total,
                "payment_method": "cash",
                "status": "completed",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_pos_sales_collection_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "POS Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "POS Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-POS-A", full_name="POS Branch A Cashier", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.posa", ["employees:view"], "Administrator")

    # Employee A creates their own sale directly (proves Phase 4's write
    # widening + auto branch-stamping from principal.branch_id together).
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers_a,
        json={"collection": "posSales", "record": {"receipt_no": "POS-A-001", "id": "POS-A-001", "customer": "Walk-in", "subtotal": 100, "vat": 5, "total": 105, "payment_method": "cash", "status": "completed"}},
    )
    assert saved.status_code == 200, saved.text
    _save_pos_sale(client, auth_headers, "POS-B-001", branch_b["id"], 200)

    listed = client.get("/api/v1/app-data/records/posSales", headers=headers_a)
    assert listed.status_code == 200, listed.text
    refs = {rec.get("receipt_no") for rec in listed.json()["records"]}
    assert "POS-A-001" in refs
    assert "POS-B-001" not in refs

    listed_admin = client.get("/api/v1/app-data/records/posSales", headers=auth_headers)
    admin_refs = {rec.get("receipt_no") for rec in listed_admin.json()["records"]}
    assert {"POS-A-001", "POS-B-001"}.issubset(admin_refs)


def test_pos_sale_auto_stamped_from_employee_branch(client, db, auth_headers):
    """The employee's own branch assignment stamps the sale automatically —
    a cashier never has to manually tag which branch they're selling for."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "POS Auto-Stamp Branch"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-POS-STAMP", full_name="Auto Stamp Cashier", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.posstamp", ["employees:view"], "Administrator")

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "posSales", "record": {"receipt_no": "POS-STAMP-001", "id": "POS-STAMP-001", "customer": "Walk-in", "subtotal": 50, "vat": 0, "total": 50, "payment_method": "cash", "status": "completed"}},
    )
    assert saved.status_code == 200, saved.text

    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key == "POS-STAMP-001").first()
    assert row is not None
    assert row.branch_id == branch["id"]


def test_sales_invoices_collection_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Sales Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Sales Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-SI-A", full_name="Sales Branch A Staff", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.sia", ["employees:view"], "Administrator")

    for ref, branch_id in (("SI-A-001", branch_a["id"]), ("SI-B-001", branch_b["id"])):
        saved = client.post(
            "/api/v1/app-data?action=save",
            headers=auth_headers,
            json={"collection": "salesInvoices", "record": {"invoice_no": ref, "customer": "Test Customer", "branch_id": branch_id, "status": "issued", "subtotal": "100.00", "total": "105.00", "vat_amount": "5.00"}},
        )
        assert saved.status_code == 200, saved.text

    listed = client.get("/api/v1/app-data/records/salesInvoices", headers=headers_a)
    assert listed.status_code == 200, listed.text
    refs = {rec.get("invoice_no") for rec in listed.json()["records"]}
    assert "SI-A-001" in refs
    assert "SI-B-001" not in refs


def test_invoices_endpoint_scoped_by_branch(client, db, auth_headers):
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Invoices Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Invoices Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-INVOICE-A", full_name="Invoices Branch A Staff", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.invoicea", ["employees:view"], "Administrator")

    created_a = client.post(
        "/api/v1/invoices",
        headers=headers_a,
        json={"customer_name": "Branch A Customer", "invoice_number": "INV-BR-A-001", "lines": [{"description": "Item", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}]},
    )
    assert created_a.status_code == 201, created_a.text
    assert created_a.json()["branch_id"] == branch_a["id"]

    created_b = client.post(
        "/api/v1/invoices",
        headers=auth_headers,
        json={"customer_name": "Branch B Customer", "invoice_number": "INV-BR-B-001", "branch_id": branch_b["id"], "lines": [{"description": "Item", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}]},
    )
    assert created_b.status_code == 201, created_b.text

    listed_a = client.get("/api/v1/invoices", headers=headers_a)
    assert listed_a.status_code == 200, listed_a.text
    numbers_a = {inv["invoice_number"] for inv in listed_a.json()}
    assert "INV-BR-A-001" in numbers_a
    assert "INV-BR-B-001" not in numbers_a

    listed_admin = client.get("/api/v1/invoices", headers=auth_headers)
    numbers_admin = {inv["invoice_number"] for inv in listed_admin.json()}
    assert {"INV-BR-A-001", "INV-BR-B-001"}.issubset(numbers_admin)


# ── Phase 7: polish — admin branch filter, serialize() fallback, backfill ──


def test_admin_branch_id_filter_scopes_collection(client, db, auth_headers):
    """The company-wide admin's opt-in ?branch_id= param (the backend half of
    the branch switcher) narrows any collection down to one branch, without
    needing that collection in _BRANCH_FILTERED_COLLECTIONS."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Filter Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Filter Branch B"}).json()

    _save_purchase_record(client, auth_headers, "PUR-FILT-A-001", branch_a["id"], "FILT-SKU-A", 1)
    _save_purchase_record(client, auth_headers, "PUR-FILT-B-001", branch_b["id"], "FILT-SKU-B", 1)

    listed_a = client.get(f"/api/v1/app-data/records/purchaseRecords?branch_id={branch_a['id']}", headers=auth_headers)
    assert listed_a.status_code == 200, listed_a.text
    refs_a = {rec["ref"] for rec in listed_a.json()["records"]}
    assert refs_a == {"PUR-FILT-A-001"}

    listed_unfiltered = client.get("/api/v1/app-data/records/purchaseRecords", headers=auth_headers)
    refs_unfiltered = {rec["ref"] for rec in listed_unfiltered.json()["records"]}
    assert {"PUR-FILT-A-001", "PUR-FILT-B-001"}.issubset(refs_unfiltered)


def test_branch_employee_cannot_escalate_via_branch_id_query_param(client, db, auth_headers):
    """A branch-scoped Employee always stays locked to their own branch, even
    if they pass ?branch_id= pointing at a different branch."""
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Escalate Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Escalate Branch B"}).json()

    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp_a = Employee(company_id=company_id, employee_no="BR-ESC-A", full_name="Escalate Branch A Staff", branch_id=branch_a["id"])
    db.add(emp_a)
    db.commit()
    headers_a = _grant_role_and_login(client, auth_headers, emp_a.id, "branchtest.esca", ["employees:view"], "Administrator")

    _save_purchase_record(client, auth_headers, "PUR-ESC-A-001", branch_a["id"], "ESC-SKU-A", 1)
    _save_purchase_record(client, auth_headers, "PUR-ESC-B-001", branch_b["id"], "ESC-SKU-B", 1)

    listed = client.get(f"/api/v1/app-data/records/purchaseRecords?branch_id={branch_b['id']}", headers=headers_a)
    assert listed.status_code == 200, listed.text
    refs = {rec["ref"] for rec in listed.json()["records"]}
    assert refs == {"PUR-ESC-A-001"}
    assert "PUR-ESC-B-001" not in refs


def test_serialize_falls_back_to_row_branch_id(client, db, auth_headers):
    """A record saved without an explicit branch_id in its JSON body still
    gets branch_id stamped onto the AppDataRecord row from the writer's own
    principal.branch_id (existing behavior) — Phase 7 makes that row-level
    value show up in the API response too, so list-view branch badges have
    something to read even when the payload itself never carried it."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Fallback Branch"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-FALLBACK", full_name="Fallback Branch Cashier", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.fallback", ["employees:view"], "Administrator")

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "posSales", "record": {"receipt_no": "POS-FALLBACK-001", "id": "POS-FALLBACK-001", "customer": "Walk-in", "subtotal": 10, "vat": 0, "total": 10, "payment_method": "cash", "status": "completed"}},
    )
    assert saved.status_code == 200, saved.text

    listed_admin = client.get("/api/v1/app-data/records/posSales", headers=auth_headers)
    record = next(rec for rec in listed_admin.json()["records"] if rec.get("receipt_no") == "POS-FALLBACK-001")
    assert record.get("branch_id") == branch["id"]


def test_employees_collection_backfills_branch_id_from_legacy_name(client, db, auth_headers):
    """Legacy "employees" records created before app.js's employee form
    started resolving branch_id by name (Phase 1) only ever got the
    free-text branch name saved. Listing the collection should lazily and
    idempotently backfill branch_id by matching that name against a real
    Branch row."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Backfill Branch"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]

    # Simulate a legacy record: JSON payload has the branch NAME but no
    # branch_id, same shape app.js wrote before Phase 1.
    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "employees",
            "record": {"id": "EMP-BACKFILL-001", "name": "Legacy Employee", "branch": "Backfill Branch", "department": "Operations"},
        },
    )
    assert saved.status_code == 200, saved.text

    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key == "EMP-BACKFILL-001", AppDataRecord.collection == "employees").first()
    assert row is not None
    assert row.branch_id is None

    listed = client.get("/api/v1/app-data/records/employees", headers=auth_headers)
    assert listed.status_code == 200, listed.text
    record = next(rec for rec in listed.json()["records"] if rec.get("id") == "EMP-BACKFILL-001")
    assert record.get("branch_id") == branch["id"]

    db.refresh(row)
    assert row.branch_id == branch["id"]


def test_branch_employee_can_list_branches_without_being_logged_out(client, db, auth_headers):
    """Regression test for a real bug found via a full browser walkthrough
    (not caught by any earlier phase's tests, since none of them simulate
    the actual hrms.html page-load sequence): GET /branches was left
    admin-only when built in Phase 1, but app.js's shared bootstrap/company-
    info hydration path (applyCompanyToUi -> applyDeptsBranchesFromCompany
    -> loadBranchesFromDb) calls it unconditionally for EVERY session,
    including an Employee/branch login landing on hrms.html. A 401 here
    trips authenticatedFetch()'s global handler, which deliberately refuses
    to auto-relogin an Employee session (to avoid masking real RBAC bugs) —
    so the employee got silently logged back out to /login moments after a
    successful login, with no error shown anywhere. Same lesson as Phase 6's
    pos.html finding: a passing backend test suite is not proof a login/auth
    UI flow actually works end-to-end."""
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "List Access Branch"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-LIST-ACCESS", full_name="List Access Branch Staff", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.listaccess", ["employees:view"], "Administrator")

    listed = client.get("/api/v1/branches", headers=headers)
    assert listed.status_code == 200, listed.text
    names = {b["name"] for b in listed.json()}
    assert "List Access Branch" in names
