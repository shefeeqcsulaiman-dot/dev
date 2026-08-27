"""Regression coverage for branch scoping on the leave endpoints. Previously
GET /leave/requests, GET /leave/balance, and every single-request action
(approve/reject/delete, plus creating a request for someone else's
employee) only ever checked company_id — a branch-locked employee with
"leave:view"/"leave:edit" could see and act on every other branch's leave
requests too."""
from app.models import Employee, LeaveRequest


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _branch_employee_headers(client, db, admin_headers, company_id, branch_id, employee_no, username, permission_keys):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"Branch Employee {employee_no}",
                    branch_id=branch_id, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)

    role_resp = client.post(
        "/api/v1/hr/admin/roles",
        headers=admin_headers,
        json={"role_name": f"Role {username}", "description": "test role", "permission_keys": permission_keys},
    )
    assert role_resp.status_code == 201, role_resp.text

    portal_resp = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": "branchtest123", "role_id": role_resp.json()["id"], "is_active": True},
    )
    assert portal_resp.status_code == 200, portal_resp.text

    login = client.post("/api/v1/ess/login", json={"username": username, "password": "branchtest123"})
    assert login.status_code == 200, login.text
    return emp, {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_branch_employee_cannot_see_or_act_on_other_branch_leave_requests(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    branch_a = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Leave Test Branch A"}).json()
    branch_b = client.post("/api/v1/branches", headers=auth_headers, json={"name": "Leave Test Branch B"}).json()

    emp_a, headers_a = _branch_employee_headers(
        client, db, auth_headers, company_id, branch_a["id"], "LV-BR-A", "leave.branch.a", ["leave:view", "leave:edit"]
    )
    emp_b, _headers_b = _branch_employee_headers(
        client, db, auth_headers, company_id, branch_b["id"], "LV-BR-B", "leave.branch.b", ["leave:view"]
    )

    # A request belonging to Branch B's employee, seeded directly (approval
    # workflow itself isn't what's under test here).
    req_b = LeaveRequest(company_id=company_id, employee_id=emp_b.id, leave_type="Annual Leave",
                          start_date="2026-09-01", end_date="2026-09-03", days=3, status="pending")
    db.add(req_b)
    db.commit()
    db.refresh(req_b)

    # Branch A's employee must not see it in the list...
    listed = client.get("/api/v1/leave/requests", headers=headers_a)
    assert listed.status_code == 200, listed.text
    assert req_b.id not in {r["id"] for r in listed.json()}

    # ...nor in the balance summary...
    balance = client.get("/api/v1/leave/balance", headers=headers_a)
    assert balance.status_code == 200, balance.text
    assert emp_b.id not in {b["employee_id"] for b in balance.json()}

    # ...nor be able to approve it directly by id.
    approve = client.post(f"/api/v1/leave/requests/{req_b.id}/approve", headers=headers_a)
    assert approve.status_code == 404, approve.text

    # The admin (unrestricted) still can.
    admin_approve = client.post(f"/api/v1/leave/requests/{req_b.id}/approve", headers=auth_headers)
    assert admin_approve.status_code == 200, admin_approve.text


def test_leave_request_creation_rejects_overlapping_dates(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    emp = Employee(company_id=company_id, employee_no="LV-OVERLAP-001", full_name="Overlap Test Employee",
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)

    first = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave",
        "start_date": "2026-10-05", "end_date": "2026-10-10",
    })
    assert first.status_code == 201, first.text

    # Overlaps the first request's range by 2 days — previously only
    # checked at approval time, so this pending-vs-pending collision would
    # have gone unnoticed until someone tried to approve one of them.
    second = client.post("/api/v1/leave/requests", headers=auth_headers, json={
        "employee_id": emp.id, "leave_type": "Annual Leave",
        "start_date": "2026-10-09", "end_date": "2026-10-12",
    })
    assert second.status_code == 409, second.text
