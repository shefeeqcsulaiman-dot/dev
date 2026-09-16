"""employees:view_salary -- a field-level add-on to employees:view. A role
that can browse the Employee Directory but lacks this permission must never
receive real basic_salary/allowance figures, through any of the three read
paths (GET /payroll/employees, the bootstrap "employees" collection, GET
/app-data/records/employees), and must not be able to write them either."""
from app.models import Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _create_role(client, headers, role_name, permission_keys):
    r = client.post(
        "/api/v1/hr/admin/roles",
        headers=headers,
        json={"role_name": role_name, "description": "test role", "permission_keys": permission_keys, "department_scope": []},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _seed_viewer_employee(db, company_id, emp_no, name):
    emp = Employee(company_id=company_id, employee_no=emp_no, full_name=name, department="Management", status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _grant_portal_access(client, headers, employee_id, username, role_id):
    r = client.put(
        f"/api/v1/hr/admin/employees/{employee_id}/portal-access",
        headers=headers,
        json={"username": username, "password": "salperm123", "role_id": role_id, "is_active": True},
    )
    assert r.status_code == 200, r.text


def _login(client, username):
    r = client.post("/api/v1/ess/login", json={"username": username, "password": "salperm123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_subject_employee(client, admin_headers, emp_no, name, salary, housing):
    """Creates the employee WHOSE salary is being viewed, via the same
    admin app-data save path the frontend uses -- populates both the
    AppDataRecord ("employees" collection, what bootstrap/list serve) and
    the mirrored SQL Employee row (what /payroll/employees serves)."""
    r = client.post(
        "/api/v1/app-data", headers=admin_headers, params={"action": "save"},
        json={"collection": "employees", "record": {
            "id": emp_no, "name": name, "department": "Operations", "designation": "Staff", "status": "Active",
            "salary": salary, "housing_allowance": housing, "transport_allowance": 100, "other_allowance": 50,
        }},
    )
    assert r.status_code == 200, r.text


def _viewer_with_permissions(client, db, admin_headers, company_id, suffix, permission_keys):
    role = _create_role(client, admin_headers, f"Sal Perm Role {suffix}", permission_keys)
    emp = _seed_viewer_employee(db, company_id, f"SALVIEW-{suffix}", f"Viewer {suffix}")
    _grant_portal_access(client, admin_headers, emp.id, f"salview.{suffix}", role["id"])
    return _login(client, f"salview.{suffix}")


def test_payroll_employees_zeroes_salary_without_permission(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALPAY-1", "Payroll Subject", 9000, 500)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "PAYNOVIEW", ["employees:view"])

    r = client.get("/api/v1/payroll/employees", headers=headers)
    assert r.status_code == 200, r.text
    row = next(e for e in r.json() if e["employee_no"] == "SALPAY-1")
    assert float(row["basic_salary"]) == 0


def test_payroll_employees_shows_salary_with_permission(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALPAY-2", "Payroll Subject 2", 9500, 500)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "PAYVIEW", ["employees:view", "employees:view_salary"])

    r = client.get("/api/v1/payroll/employees", headers=headers)
    assert r.status_code == 200, r.text
    row = next(e for e in r.json() if e["employee_no"] == "SALPAY-2")
    assert float(row["basic_salary"]) == 9500


def test_bootstrap_employees_redacts_salary_without_permission(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALBOOT-1", "Bootstrap Subject", 8000, 400)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "BOOTNOVIEW", ["employees:view"])

    r = client.get("/api/v1/app-data", headers=headers, params={"scope": "hrms"})
    assert r.status_code == 200, r.text
    employees = r.json()["data"].get("employees", [])
    row = next(e for e in employees if e.get("id") == "SALBOOT-1")
    assert row["salary"] is None
    assert row["housing_allowance"] is None
    assert row["transport_allowance"] is None
    assert row["other_allowance"] is None


def test_bootstrap_employees_shows_salary_with_permission(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALBOOT-2", "Bootstrap Subject 2", 8500, 450)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "BOOTVIEW", ["employees:view", "employees:view_salary"])

    r = client.get("/api/v1/app-data", headers=headers, params={"scope": "hrms"})
    assert r.status_code == 200, r.text
    employees = r.json()["data"].get("employees", [])
    row = next(e for e in employees if e.get("id") == "SALBOOT-2")
    assert float(row["salary"]) == 8500


def test_app_data_records_employees_403_without_employees_view(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "NOVIEWAT", [])

    r = client.get("/api/v1/app-data/records/employees", headers=headers)
    assert r.status_code == 403, r.text


def test_app_data_records_employees_redacts_salary(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALREC-1", "Records Subject", 7000, 300)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "RECNOVIEW", ["employees:view"])

    r = client.get("/api/v1/app-data/records/employees", headers=headers)
    assert r.status_code == 200, r.text
    row = next(rec for rec in r.json()["records"] if rec.get("id") == "SALREC-1")
    assert row["salary"] is None


def test_write_side_ignores_salary_fields_without_permission(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _seed_subject_employee(client, auth_headers, "SALWRITE-1", "Write Subject", 6000, 200)
    headers = _viewer_with_permissions(client, db, auth_headers, company_id, "WRITENOVIEW", ["employees:view"])

    # Attempt to overwrite the subject's salary as a principal denied
    # employees:view_salary -- must be silently ignored, not applied.
    r = client.post(
        "/api/v1/app-data", headers=headers, params={"action": "save"},
        json={"collection": "employees", "record": {
            "id": "SALWRITE-1", "name": "Write Subject", "department": "Operations",
            "salary": 999999, "housing_allowance": 0,
        }},
    )
    assert r.status_code == 200, r.text

    emp = db.query(Employee).filter(Employee.company_id == company_id, Employee.employee_no == "SALWRITE-1").first()
    assert float(emp.basic_salary) == 6000
    assert float(emp.housing_allowance) == 200


def test_admin_user_always_sees_real_salary_regardless_of_role(client, db, auth_headers):
    _seed_subject_employee(client, auth_headers, "SALADMIN-1", "Admin View Subject", 12000, 600)

    r = client.get("/api/v1/payroll/employees", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(e for e in r.json() if e["employee_no"] == "SALADMIN-1")
    assert float(row["basic_salary"]) == 12000

    r = client.get("/api/v1/app-data", headers=auth_headers, params={"scope": "hrms"})
    assert r.status_code == 200, r.text
    employees = r.json()["data"].get("employees", [])
    row = next(e for e in employees if e.get("id") == "SALADMIN-1")
    assert float(row["salary"]) == 12000
