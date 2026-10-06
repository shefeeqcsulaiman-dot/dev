"""Permissions are enforced on the server, not just hidden in the UI:
- main-app user roles (Settings > Users & Roles): viewer / sales / accountant / manager get only
  their modules; admin-only actions stay admin-only; the legacy "user" role keeps full access
- employee logins need the module's permission for sales invoices and stock
- a non-admin can't grant (create / edit / assign) more permissions than they hold
- the role list is for people who manage roles"""
import uuid

from sqlalchemy import text

from app.company_defaults import backfill_user_roles_from_ui
from app.models import AppDataRecord, Employee, Role, User
from tests.test_hrms_department_scope import _company_id, _login_as


def _user(client, admin, role):
    tag = uuid.uuid4().hex[:6]
    email = f"{role}-{tag}@example.com"
    r = client.post("/api/v1/companies/current/users", headers=admin, json={"email": email, "full_name": role.title(), "password": "rolepass123", "role": role})
    assert r.status_code == 201, r.text
    tok = client.post("/api/v1/auth/login", json={"email": email, "password": "rolepass123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}, tag


def _save(client, h, collection, record):
    return client.post("/api/v1/app-data?action=save", headers=h, json={"collection": collection, "record": record}).status_code


def _invoice(tag):
    return {"invoice_no": f"RP-{tag}", "customer": "C", "date": "2026-10-01", "status": "Draft", "lines": [{"description": "x", "qty": 1, "unit_price": 10}]}


def _purchase(tag):
    return {"ref": f"RPP-{tag}", "supplier": "S", "subtotal": 10, "vat_amount": 0.5, "total": 10.5}


def _admin_only_checks(client, h):
    return {
        "company profile": client.put("/api/v1/companies/current", headers=h, json={"phone": "0500000001"}).status_code,
        "backup": client.get("/api/v1/app-data/db-dump", headers=h).status_code,
        "user list": client.get("/api/v1/app-data/users", headers=h).status_code,
        "new branch": client.post("/api/v1/branches", headers=h, json={"name": "Nope"}).status_code,
        "payroll": client.get("/api/v1/payroll/runs", headers=h).status_code,
        "hr role": client.post("/api/v1/hr/admin/roles", headers=h, json={"role_name": f"X{uuid.uuid4().hex[:4]}", "description": "x", "permission_keys": ["dashboard:view"]}).status_code,
    }


def test_viewer_is_read_only(client, auth_headers):
    h, tag = _user(client, auth_headers, "viewer")
    assert client.get("/api/v1/app-data/records/salesInvoices", headers=h).status_code == 200
    assert client.get("/api/v1/reports/dashboard", headers=h).status_code == 200
    assert _save(client, h, "salesInvoices", _invoice(tag)) == 403
    assert _save(client, h, "purchaseRecords", _purchase(tag)) == 403
    assert _save(client, h, "products", {"code": f"RP-{tag}", "name": "x"}) == 403
    assert client.post("/api/v1/app-data?action=delete", headers=h, json={"collection": "salesInvoices", "record": {"invoice_no": "anything"}}).status_code == 403
    assert client.get("/api/v1/app-data/records/bankAccounts", headers=h).status_code == 403  # bank isn't in the viewer's pages
    assert set(_admin_only_checks(client, h).values()) == {403}


def test_sales_role(client, auth_headers):
    h, tag = _user(client, auth_headers, "sales")
    assert _save(client, h, "salesInvoices", _invoice(tag)) == 200
    assert _save(client, h, "customers", {"name": f"Cust {tag}"}) == 200
    assert _save(client, h, "purchaseRecords", _purchase(tag)) == 403
    assert client.get("/api/v1/app-data/records/purchaseRecords", headers=h).status_code == 403
    assert set(_admin_only_checks(client, h).values()) == {403}


def test_accountant_role(client, auth_headers):
    h, tag = _user(client, auth_headers, "accountant")
    assert _save(client, h, "purchaseRecords", _purchase(tag)) == 200
    assert _save(client, h, "salesInvoices", _invoice(tag)) == 200
    assert _save(client, h, "posSales", {"id": f"RPOS-{tag}", "receipt_no": f"RPOS-{tag}", "status": "completed", "total": 1}) == 403
    assert client.get("/api/v1/accounts", headers=h).status_code == 200
    assert set(_admin_only_checks(client, h).values()) == {403}


def test_manager_role(client, auth_headers):
    h, tag = _user(client, auth_headers, "manager")
    assert _save(client, h, "purchaseRecords", _purchase(tag)) == 200
    assert _save(client, h, "posSales", {"id": f"MPOS-{tag}", "receipt_no": f"MPOS-{tag}", "status": "completed", "total": 1}) == 200
    assert set(_admin_only_checks(client, h).values()) == {403}


def test_admin_and_legacy_user_keep_full_access(client, auth_headers):
    for role in ("admin", "user"):
        h, tag = _user(client, auth_headers, role)
        checks = _admin_only_checks(client, h)
        assert checks["company profile"] == 200 and checks["payroll"] == 200, (role, checks)
        assert _save(client, h, "salesInvoices", _invoice(tag)) == 200


def test_employee_logins_need_the_module_permission(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6].upper()
    hr_only = _login_as(client, db, auth_headers, cid, tag, [], ["dashboard:view"])
    assert client.get("/api/v1/invoices", headers=hr_only).status_code == 403
    assert client.post("/api/v1/invoices", headers=hr_only, json={"invoice_number": f"EMP-{tag}", "customer_name": "x", "lines": []}).status_code == 403
    for path in ("/api/v1/inventory/mappings", "/api/v1/inventory/stock-levels", "/api/v1/inventory/stock-movements"):
        assert client.get(path, headers=hr_only).status_code == 403, path
    assert _save(client, hr_only, "salesInvoices", _invoice(tag)) == 403

    sales = _login_as(client, db, auth_headers, cid, tag + "S", [], ["sales:view"])
    assert client.get("/api/v1/invoices", headers=sales).status_code == 200
    assert _save(client, sales, "salesInvoices", _invoice(tag + "S")) == 403  # view only
    editor = _login_as(client, db, auth_headers, cid, tag + "E", [], ["sales:view", "sales:edit"])
    assert _save(client, editor, "salesInvoices", _invoice(tag + "E")) == 200
    delete = {"collection": "salesInvoices", "record": {"invoice_no": f"RP-{tag}E"}}
    assert client.post("/api/v1/app-data?action=delete", headers=editor, json=delete).status_code == 403  # no delete
    remover = _login_as(client, db, auth_headers, cid, tag + "D", [], ["sales:view", "sales:edit", "sales:delete"])
    assert client.post("/api/v1/app-data?action=delete", headers=remover, json=delete).status_code == 200
    pos = _login_as(client, db, auth_headers, cid, tag + "P", [], ["pos:view", "pos:edit"])
    assert client.get("/api/v1/inventory/stock-levels", headers=pos).status_code == 200   # POS stock warning
    assert _save(client, pos, "salesInvoices", _invoice(tag + "P")) == 200                # POS sale's invoice


def test_portal_login_without_role_gets_no_business_data(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    emp = Employee(company_id=cid, employee_no=f"NR-{tag}", full_name="No Role", status="active")
    db.add(emp); db.commit(); db.refresh(emp)
    client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers, json={"username": f"nr.{tag}", "password": "norole123", "is_active": True})
    h = {"Authorization": "Bearer " + client.post("/api/v1/ess/login", json={"username": f"nr.{tag}", "password": "norole123"}).json()["access_token"]}
    assert client.get("/api/v1/invoices", headers=h).status_code == 403
    assert client.get("/api/v1/inventory/stock-levels", headers=h).status_code == 403
    assert client.get("/api/v1/hr/roles", headers=h).status_code == 403


def _hr_manager(client, db, auth_headers, cid, tag):
    client.get("/api/v1/hr/admin/roles", headers=auth_headers)  # seeds the built-in roles
    hrm = db.query(Role).filter(Role.company_id == cid, Role.role_name == "HR Manager").first()
    emp = Employee(company_id=cid, employee_no=f"HRM-{tag}", full_name="HR Mgr", status="active")
    db.add(emp); db.commit(); db.refresh(emp)
    client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=auth_headers,
               json={"username": f"hrm.{tag}", "password": "hrmgr12345", "role_id": hrm.id, "is_active": True})
    h = {"Authorization": "Bearer " + client.post("/api/v1/ess/login", json={"username": f"hrm.{tag}", "password": "hrmgr12345"}).json()["access_token"]}
    return emp, h


def test_hr_manager_cannot_grant_more_than_they_hold(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    emp, h = _hr_manager(client, db, auth_headers, cid, tag)
    admin_role = db.query(Role).filter(Role.company_id == cid, Role.role_name == "Administrator").first()

    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=h, json={"role_id": admin_role.id})
    assert r.status_code == 403, r.text
    r = client.post("/api/v1/hr/admin/roles", headers=h, json={"role_name": f"Payroll {tag}", "description": "x", "permission_keys": ["payroll:view", "payroll:run_payroll"]})
    assert r.status_code == 403 and "payroll" in r.json()["detail"], r.text
    assert client.get("/api/v1/payroll/runs", headers=h).status_code == 200  # HR Manager views payroll
    assert client.post("/api/v1/payroll/generate", headers=h, json={"period": "2032-01"}).status_code == 403  # but can't run it

    # granting what they do hold still works
    r = client.post("/api/v1/hr/admin/roles", headers=h, json={"role_name": f"Leave Desk {tag}", "description": "x", "permission_keys": ["leave:view", "leave:edit"]})
    assert r.status_code == 201, r.text
    other = Employee(company_id=cid, employee_no=f"LD-{tag}", full_name="Leave Desk", status="active")
    db.add(other); db.commit(); db.refresh(other)
    r = client.put(f"/api/v1/hr/admin/employees/{other.id}/portal-access", headers=h,
                   json={"username": f"ld.{tag}", "password": "leavedesk1", "role_id": r.json()["id"], "is_active": True})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/hr/roles", headers=h).status_code == 200  # manages roles


def test_department_scoped_login_cannot_create_company_wide_role(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6].upper()
    h = _login_as(client, db, auth_headers, cid, tag, ["Sales"], ["hr_settings:view", "hr_settings:edit", "leave:view"])
    base = {"description": "x", "permission_keys": ["leave:view"]}
    assert client.post("/api/v1/hr/admin/roles", headers=h, json={**base, "role_name": f"Wide {tag}", "department_scope": []}).status_code == 403
    assert client.post("/api/v1/hr/admin/roles", headers=h, json={**base, "role_name": f"Other {tag}", "department_scope": ["Finance"]}).status_code == 403
    assert client.post("/api/v1/hr/admin/roles", headers=h, json={**base, "role_name": f"Own {tag}", "department_scope": ["Sales"]}).status_code == 201


def test_existing_logins_get_the_role_picked_on_screen(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    rows = {"Manager": f"m-{tag}@example.com", "Sales": f"s-{tag}@example.com", "Admin": f"a-{tag}@example.com"}
    for label, email in rows.items():
        db.add(User(company_id=cid, email=email, full_name=label, password_hash="x", role="user"))
        db.add(AppDataRecord(company_id=cid, collection="users", record_key=f"U-{label}-{tag}",
                             payload=f'{{"email": "{email}", "role": "{label}"}}'))
    orphan = f"o-{tag}@example.com"
    db.add(User(company_id=cid, email=orphan, full_name="Orphan", password_hash="x", role="user"))
    db.commit()
    db.execute(text("CREATE TABLE IF NOT EXISTS schema_flags (name VARCHAR(80) PRIMARY KEY)"))
    db.execute(text("DELETE FROM schema_flags WHERE name = 'user_roles_from_ui_v1'"))
    db.commit()
    backfill_user_roles_from_ui(db)
    db.expire_all()
    got = {u.email: u.role for u in db.query(User).filter(User.email.in_(list(rows.values()) + [orphan])).all()}
    assert got == {rows["Manager"]: "manager", rows["Sales"]: "sales", rows["Admin"]: "admin", orphan: "user"}


def test_upgrade_keeps_existing_roles_able_to_change_what_they_could_view(client, db, auth_headers):
    from app.routers.hr_access import backfill_main_module_edit
    tag = uuid.uuid4().hex[:6]
    viewer = client.post("/api/v1/hr/admin/roles", headers=auth_headers, json={"role_name": f"Old Sales {tag}", "description": "x", "permission_keys": ["sales:view"]}).json()
    hr = client.post("/api/v1/hr/admin/roles", headers=auth_headers, json={"role_name": f"Old HR {tag}", "description": "x", "permission_keys": ["leave:view"]}).json()
    db.execute(text("DELETE FROM schema_flags WHERE name = 'main_module_edit_v1'"))
    db.commit()
    backfill_main_module_edit(db)
    roles = {r["id"]: set(r["permissions"]) for r in client.get("/api/v1/hr/admin/roles", headers=auth_headers).json()}
    assert {"sales:view", "sales:edit", "sales:delete"} <= roles[viewer["id"]]
    assert roles[hr["id"]] == {"leave:view"}
