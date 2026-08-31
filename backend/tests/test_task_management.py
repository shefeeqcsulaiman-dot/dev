"""Task Management (HRMS) round-trips through the generic AppDataRecord
'tasks' collection, same pattern as employeeLoans/jobRequisitions. Also
covers the /payroll/employees active-only fix -- that endpoint feeds the
Task assignee dropdown (and Leave's/GPS's employee pickers), and previously
returned Inactive staff too."""
from app.models import Employee


def _save_task(client, headers, record):
    return client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "tasks", "record": record},
    )


def _seed_employee(db, company_id, employee_no, full_name, status="active"):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=full_name,
                    basic_salary=5000, status=status)
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def test_task_save_list_and_delete(client, auth_headers):
    record = {
        "id": "TASK-TEST-001", "title": "Prepare Q3 summary", "description": "For payroll review",
        "assigned_to": "", "assigned_to_name": "", "priority": "High",
        "due_date": "2026-09-15", "status": "todo",
    }
    r = _save_task(client, auth_headers, record)
    assert r.status_code == 200, r.text

    r2 = client.get("/api/v1/app-data/records/tasks", headers=auth_headers)
    assert r2.status_code == 200, r2.text
    tasks = r2.json()["records"]
    saved = next((t for t in tasks if t.get("id") == "TASK-TEST-001"), None)
    assert saved is not None
    assert saved["title"] == "Prepare Q3 summary"
    assert saved["priority"] == "High"

    # Move to a different status (quick move, same as moveTaskStatus() does).
    updated = {**record, "status": "in_progress"}
    r3 = _save_task(client, auth_headers, updated)
    assert r3.status_code == 200, r3.text
    tasks2 = client.get("/api/v1/app-data/records/tasks", headers=auth_headers).json()["records"]
    saved2 = next(t for t in tasks2 if t["id"] == "TASK-TEST-001")
    assert saved2["status"] == "in_progress"

    r4 = client.post(
        "/api/v1/app-data?action=delete",
        headers=auth_headers,
        json={"collection": "tasks", "record": {"id": "TASK-TEST-001"}},
    )
    assert r4.status_code == 200, r4.text
    tasks3 = client.get("/api/v1/app-data/records/tasks", headers=auth_headers).json()["records"]
    assert not any(t["id"] == "TASK-TEST-001" for t in tasks3)


def test_task_isolated_between_companies(client, auth_headers, second_tenant_headers):
    _save_task(client, auth_headers, {"id": "TASK-ISO-001", "title": "Isolated task", "status": "todo"})
    r = client.get("/api/v1/app-data/records/tasks", headers=second_tenant_headers)
    tasks = r.json()["records"]
    assert not any(t.get("id") == "TASK-ISO-001" for t in tasks)


def test_payroll_employees_excludes_inactive(client, db, auth_headers):
    company_id = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["id"]
    _seed_employee(db, company_id, "TASK-ACTIVE-1", "Active Task Assignee", status="active")
    _seed_employee(db, company_id, "TASK-INACTIVE-1", "Inactive Task Assignee", status="inactive")

    r = client.get("/api/v1/payroll/employees", headers=auth_headers)
    assert r.status_code == 200, r.text
    names = [e["full_name"] for e in r.json()]
    assert "Active Task Assignee" in names
    assert "Inactive Task Assignee" not in names
