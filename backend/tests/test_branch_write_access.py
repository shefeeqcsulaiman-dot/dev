"""Branch Management, Phase 4: an Employee (branch/HRMS sub-user) principal
can now read and write through the main AppDataRecord endpoints
(GET /app-data/records/{collection}, POST /app-data with save/bulk-save/
delete actions) — previously these were get_current_user-only, so an
Employee token could not create a POS sale or purchase at all. This phase
is deliberately NOT about data isolation (that's Phases 5-6, once
AppDataRecord.branch_id is actually filtered on) — every test here just
proves the write path now WORKS for an Employee, and that the admin (User)
path is completely unaffected.
"""

from app.models import AppDataRecord, AuditLog, Employee


def _grant_role_and_login(client, admin_headers, employee_id, username, permission_keys, role_name):
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


def test_employee_can_save_and_read_app_data_record(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-WRITE-A", full_name="Write Access Employee")
    db.add(emp)
    db.commit()
    emp_headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.write1", ["employees:view", "inventory:view", "inventory:edit", "inventory:delete"], "Write Test Role")

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=emp_headers,
        json={"collection": "products", "record": {"code": "BR-WRITE-SKU-1", "name": "Branch Write Test Product"}},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] is True

    listed = client.get("/api/v1/app-data/records/products", headers=emp_headers)
    assert listed.status_code == 200, listed.text
    codes = [r.get("code") for r in listed.json()["records"]]
    assert "BR-WRITE-SKU-1" in codes


def test_employee_write_attributed_to_employee_not_user_in_audit_log(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-WRITE-B", full_name="Audit Trail Employee")
    db.add(emp)
    db.commit()
    emp_headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.write2", ["employees:view", "inventory:view", "inventory:edit", "inventory:delete"], "Write Test Role")

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=emp_headers,
        json={"collection": "products", "record": {"code": "BR-WRITE-SKU-2", "name": "Audit Trail Product"}},
    )
    assert saved.status_code == 200, saved.text

    log = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == company_id, AuditLog.action == "record_saved")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log is not None
    assert log.employee_id == emp.id
    assert log.user_id is None


def test_admin_write_still_attributed_to_user_not_employee(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    user_id = r.json()["id"]

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "products", "record": {"code": "BR-WRITE-SKU-ADMIN", "name": "Admin Written Product"}},
    )
    assert saved.status_code == 200, saved.text

    log = (
        db.query(AuditLog)
        .filter(AuditLog.company_id == company_id, AuditLog.action == "record_saved")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    assert log is not None
    assert log.user_id == user_id
    assert log.employee_id is None


def test_employee_save_stamps_appdata_record_branch_id(client, db, auth_headers):
    branch = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Write Test Branch"}).json()
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-WRITE-C", full_name="Branch Stamped Employee", branch_id=branch["id"])
    db.add(emp)
    db.commit()
    emp_headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.write3", ["employees:view", "inventory:view", "inventory:edit", "inventory:delete"], "Write Test Role")

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=emp_headers,
        json={"collection": "products", "record": {"code": "BR-WRITE-SKU-3", "name": "Branch Stamped Product"}},
    )
    assert saved.status_code == 200, saved.text

    row = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key == "BR-WRITE-SKU-3").first()
    assert row is not None
    # Stamped now for later phases to filter on — not itself an isolation
    # guarantee yet (another branch's Employee can still see/edit this today).
    assert row.branch_id == branch["id"]


def test_employee_bulk_save_and_delete_work(client, db, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-WRITE-D", full_name="Bulk Write Employee")
    db.add(emp)
    db.commit()
    emp_headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.write4", ["employees:view", "inventory:view", "inventory:edit", "inventory:delete"], "Write Test Role")

    bulk = client.post(
        "/api/v1/app-data?action=bulk-save",
        headers=emp_headers,
        json={"collection": "products", "records": [
            {"code": "BR-WRITE-BULK-1", "name": "Bulk Product 1"},
            {"code": "BR-WRITE-BULK-2", "name": "Bulk Product 2"},
        ]},
    )
    assert bulk.status_code == 200, bulk.text
    assert bulk.json()["saved"] == 2

    deleted = client.post(
        "/api/v1/app-data?action=delete",
        headers=emp_headers,
        json={"collection": "products", "record": {"code": "BR-WRITE-BULK-1"}},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] is True


def test_wipe_company_data_stays_admin_only(client, db, auth_headers):
    """This destructive endpoint was deliberately NOT widened in Phase 4 —
    confirm an Employee token still can't reach it."""
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    company_id = r.json()["company"]["id"]
    emp = Employee(company_id=company_id, employee_no="BR-WRITE-E", full_name="No Wipe Access Employee")
    db.add(emp)
    db.commit()
    emp_headers = _grant_role_and_login(client, auth_headers, emp.id, "branchtest.write5", ["employees:view", "inventory:view", "inventory:edit", "inventory:delete"], "Write Test Role")

    resp = client.post("/api/v1/app-data/wipe", headers=emp_headers, json={"confirm": "DELETE ALL"})
    assert resp.status_code in (401, 403)
