"""Add Custom Role's "Departments" field: a role scoped to one or more
departments, once granted to an employee via ESS Portal Access, gives that
employee a Team roster (GET /ess/team) of every employee in those
departments -- no separate permission checkbox needed."""
from app.models import Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _seed_employee(db, company_id, emp_no, name, department="Operations"):
    emp = Employee(company_id=company_id, employee_no=emp_no, full_name=name, department=department)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _create_role(client, headers, role_name, department_scope):
    r = client.post(
        "/api/v1/hr/admin/roles",
        headers=headers,
        json={"role_name": role_name, "description": "test role", "permission_keys": [], "department_scope": department_scope},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _grant_portal_access(client, headers, employee_id, username, role_id):
    r = client.put(
        f"/api/v1/hr/admin/employees/{employee_id}/portal-access",
        headers=headers,
        json={"username": username, "password": "deptteam123", "role_id": role_id, "is_active": True},
    )
    assert r.status_code == 200, r.text


def _login(client, username):
    r = client.post("/api/v1/ess/login", json={"username": username, "password": "deptteam123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_role_department_scope_round_trips_through_admin_crud(client, auth_headers):
    role = _create_role(client, auth_headers, "Dept Scope Round Trip", ["Finance", "Sales"])
    assert sorted(role["department_scope"]) == ["Finance", "Sales"]

    r = client.get("/api/v1/hr/admin/roles", headers=auth_headers)
    assert r.status_code == 200, r.text
    fetched = next(x for x in r.json() if x["id"] == role["id"])
    assert sorted(fetched["department_scope"]) == ["Finance", "Sales"]

    r = client.put(
        f"/api/v1/hr/admin/roles/{role['id']}",
        headers=auth_headers,
        json={"role_name": "Dept Scope Round Trip", "permission_keys": [], "department_scope": ["Finance"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["department_scope"] == ["Finance"]


def test_role_with_no_department_scope_defaults_to_empty(client, auth_headers):
    role = _create_role(client, auth_headers, "No Dept Scope", [])
    assert role["department_scope"] == []


def test_ess_team_returns_employees_in_scoped_departments(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    finance_emp = _seed_employee(db, company_id, "DEPT-FIN-1", "Finance One", department="Finance")
    sales_emp = _seed_employee(db, company_id, "DEPT-SALES-1", "Sales One", department="Sales")
    ops_emp = _seed_employee(db, company_id, "DEPT-OPS-1", "Ops One", department="Operations")
    manager = _seed_employee(db, company_id, "DEPT-MGR-1", "Finance Manager", department="Management")

    role = _create_role(client, auth_headers, "Finance Team Lead", ["Finance", "Sales"])
    _grant_portal_access(client, auth_headers, manager.id, "dept.team.manager", role["id"])
    headers = _login(client, "dept.team.manager")

    r = client.get("/api/v1/ess/team", headers=headers)
    assert r.status_code == 200, r.text
    # auth_headers reuses one shared test company for the whole pytest
    # session (see the "Shared Test-Company Pollution" convention this
    # suite follows) -- another file may have already seeded an unrelated
    # employee into "Finance" or "Sales", so assert our two are present and
    # the Operations employee is excluded, rather than an exact set match.
    names = {row["full_name"] for row in r.json()}
    assert {"Finance One", "Sales One"}.issubset(names)
    assert ops_emp.full_name not in names


def test_ess_team_excludes_inactive_employees(client, auth_headers, db):
    """GET /ess/team previously returned every employee in a scoped
    department regardless of status -- a former employee's row is kept
    around for history (payroll, past attendance) but has no business
    appearing in a live "who's on my team" roster, same as the equivalent
    fix already applied to Rota's staff list and Monthly Staff Overview."""
    company_id = _company_id(client, auth_headers)
    active_emp = _seed_employee(db, company_id, "DEPT-ACTIVE-1", "Still Here", department="Warehouse")
    inactive_emp = _seed_employee(db, company_id, "DEPT-INACTIVE-1", "Long Gone", department="Warehouse")
    inactive_emp.status = "inactive"
    db.commit()
    manager = _seed_employee(db, company_id, "DEPT-MGR-2", "Warehouse Manager", department="Management")

    role = _create_role(client, auth_headers, "Warehouse Team Lead 2", ["Warehouse"])
    _grant_portal_access(client, auth_headers, manager.id, "dept.team.manager2", role["id"])
    headers = _login(client, "dept.team.manager2")

    r = client.get("/api/v1/ess/team", headers=headers)
    assert r.status_code == 200, r.text
    names = {row["full_name"] for row in r.json()}
    assert active_emp.full_name in names
    assert inactive_emp.full_name not in names


def test_ess_team_403_when_role_has_no_department_scope(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "DEPT-NOSCOPE-1", "No Scope Employee")
    role = _create_role(client, auth_headers, "No Scope Role", [])
    _grant_portal_access(client, auth_headers, emp.id, "dept.noscope.user", role["id"])
    headers = _login(client, "dept.noscope.user")

    r = client.get("/api/v1/ess/team", headers=headers)
    assert r.status_code == 403, r.text


def test_admin_employees_list_reports_assigned_role_department_scope(client, auth_headers, db):
    """The Users & Roles screen (GET /hr/admin/employees) shows exactly what
    an employee will see in ESS's Team tab once portal access is granted --
    so an admin doesn't have to cross-reference Roles & Permissions."""
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "DEPT-ADMINLIST-1", "Admin List Employee", department="Warehouse")
    role = _create_role(client, auth_headers, "Warehouse Team Lead", ["Warehouse"])
    _grant_portal_access(client, auth_headers, emp.id, "dept.adminlist.user", role["id"])

    r = client.get("/api/v1/hr/admin/employees", headers=auth_headers)
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["id"] == emp.id)
    assert row["department_scope"] == ["Warehouse"]


def test_hr_me_reports_department_scope(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    emp = _seed_employee(db, company_id, "DEPT-HRME-1", "HR Me Employee", department="HR")
    role = _create_role(client, auth_headers, "HR Me Scoped Role", ["HR"])
    _grant_portal_access(client, auth_headers, emp.id, "dept.hrme.user", role["id"])
    headers = _login(client, "dept.hrme.user")

    r = client.get("/api/v1/hr/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["department_scope"] == ["HR"]
