"""Employee (ESS/RBAC) principals must not read or write company-wide data
through the generic /app-data endpoints beyond what their role grants."""
from tests.test_employees_salary_permission import (
    _company_id, _login, _seed_subject_employee, _seed_viewer_employee, _viewer_with_permissions,
)


def _norole_login(client, db, admin_headers, company_id, suffix):
    emp = _seed_viewer_employee(db, company_id, f"SECNR-{suffix}", f"NoRole {suffix}")
    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers,
        json={"username": f"secnr.{suffix}", "password": "salperm123", "is_active": True},
    )
    assert r.status_code == 200, r.text
    return _login(client, f"secnr.{suffix}")


def _save(client, headers, collection, record):
    return client.post("/api/v1/app-data", headers=headers, params={"action": "save"},
                       json={"collection": collection, "record": record})


def test_roleless_employee_cannot_write_company_data(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    h = _norole_login(client, db, auth_headers, cid, "w1")
    for coll in ("customers", "salesInvoices", "ledger", "users", "companyAnnouncements"):
        assert _save(client, h, coll, {"id": f"X-{coll}", "name": "x"}).status_code == 403, coll
    r = client.post("/api/v1/app-data", headers=h, params={"action": "invoice-layout"}, json={"title": "x"})
    assert r.status_code == 403


def test_roleless_employee_cannot_list_collections(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    assert _save(client, auth_headers, "salaryAdvances", {"id": "ADV-SEC-1", "employee": "A", "amount": 5}).status_code == 200
    h = _norole_login(client, db, auth_headers, cid, "r1")
    assert client.get("/api/v1/app-data/records/salaryAdvances", headers=h).status_code == 403
    assert client.get("/api/v1/app-data/records/customers", headers=h).status_code == 403


def test_non_hr_role_cannot_list_hr_collections(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    h = _viewer_with_permissions(client, db, auth_headers, cid, "SECSALES", ["sales:view"])
    assert client.get("/api/v1/app-data/records/salaryAdvances", headers=h).status_code == 403
    assert client.get("/api/v1/app-data/records/customers", headers=h).status_code == 200


def test_view_only_role_cannot_change_salary_iban_or_create_employee(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SECEMP-1", "Sec Subject", 6000, 200)
    h = _viewer_with_permissions(client, db, auth_headers, cid, "SECVIEW", ["employees:view", "employees:view_salary"])
    r = _save(client, h, "employees", {
        "id": "SECEMP-1", "name": "Sec Subject", "department": "Ops",
        "salary": 1, "iban": "AE070331234567890123456", "status": "Inactive",
    })
    assert r.status_code == 200, r.text
    rows = client.get("/api/v1/payroll/employees", headers=auth_headers).json()
    emp = next(e for e in rows if e["employee_no"] == "SECEMP-1")
    assert float(emp["basic_salary"]) == 6000
    assert not emp["iban"]
    assert _save(client, h, "employees", {"id": "SECEMP-NEW", "name": "Ghost"}).status_code == 403


def test_employee_iban_and_salary_validation(client, auth_headers):
    bad = _save(client, auth_headers, "employees", {"id": "SECEMP-V", "name": "V", "salary": -5})
    assert bad.status_code == 422
    bad = _save(client, auth_headers, "employees", {"id": "SECEMP-V", "name": "V", "salary": 100, "iban": "XX"})
    assert bad.status_code == 422
    ok = _save(client, auth_headers, "employees", {"id": "SECEMP-V", "name": "V", "salary": 100, "iban": "AE07 0331 2345 6789 0123 456"})
    assert ok.status_code == 200
    cleared = _save(client, auth_headers, "employees", {"id": "SECEMP-V", "name": "V", "salary": 100, "iban": ""})
    assert cleared.status_code == 200
    rows = client.get("/api/v1/payroll/employees", headers=auth_headers).json()
    assert not next(e for e in rows if e["employee_no"] == "SECEMP-V")["iban"]
