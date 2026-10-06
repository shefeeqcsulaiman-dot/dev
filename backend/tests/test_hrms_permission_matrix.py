"""HRMS role permissions, module by module: no access, view only, edit, and edit + delete.

Also covers the built-in Manager role's rota edit and payroll runs by non-admin roles."""
import uuid

import pytest
from sqlalchemy import text

from app.models import Permission, Role, RolePermission
from app.routers.hr_access import _DEFAULT_ROLES_FLAG, backfill_default_role_permissions
from tests.test_hrms_department_scope import _company_id, _delete, _login_as, _save

# collection -> (module, a record the module's editors may create)
MODULES = {
    "rotaAssignments": ("rota", lambda t: {"id": f"PM-RA-{t}", "employee_id": f"PM-{t}", "date": "2026-09-30", "code": "M"}),
    "rotaShifts": ("rota", lambda t: {"id": f"PM{t}", "code": f"PM{t}", "name": "Matrix", "start": "09:00", "end": "17:00"}),
    "overtimeRequests": ("overtime", lambda t: {"id": f"PM-OT-{t}", "employee_id": f"PM-{t}", "date": "2026-09-01", "ot_hours": 1, "status": "Pending"}),
    "employeeLoans": ("loans", lambda t: {"id": f"PM-LN-{t}", "employee_id": f"PM-{t}", "amount": 100, "status": "Pending"}),
    "tasks": ("hr_workflow", lambda t: {"id": f"PM-T-{t}", "title": "Matrix task", "status": "todo"}),
    "candidates": ("recruitment", lambda t: {"id": f"PM-C-{t}", "name": "Matrix Candidate", "status": "Applied"}),
}


def _list(client, headers, collection):
    return client.get(f"/api/v1/app-data/records/{collection}", headers=headers)


def _bootstrap_has(client, headers, collection):
    body = client.get("/api/v1/app-data", headers=headers).json()
    data = body.get("data", body)
    return bool(data.get(collection))


def _tag():
    return uuid.uuid4().hex[:6].upper()


@pytest.mark.parametrize("collection", list(MODULES))
def test_view_edit_delete_levels(client, db, auth_headers, collection):
    module, make = MODULES[collection]
    cid = _company_id(client, auth_headers)

    none_h = _login_as(client, db, auth_headers, cid, _tag(), [], ["dashboard:view", "leave:view" if module != "leave" else "rota:view"])
    assert _list(client, none_h, collection).status_code == 403
    assert not _bootstrap_has(client, none_h, collection)
    assert _save(client, none_h, collection, make(_tag())).status_code == 403

    view_h = _login_as(client, db, auth_headers, cid, _tag(), [], [f"{module}:view"])
    assert _list(client, view_h, collection).status_code == 200
    r = _save(client, view_h, collection, make(_tag()))
    assert r.status_code == 403
    assert "Edit" in r.json()["detail"]

    edit_h = _login_as(client, db, auth_headers, cid, _tag(), [], [f"{module}:view", f"{module}:edit"])
    rec = make(_tag())
    assert _save(client, edit_h, collection, rec).status_code == 200
    r = _delete(client, edit_h, collection, rec)
    assert r.status_code == 403
    assert "Delete" in r.json()["detail"]

    del_h = _login_as(client, db, auth_headers, cid, _tag(), [], [f"{module}:view", f"{module}:edit", f"{module}:delete"])
    assert _delete(client, del_h, collection, rec).status_code == 200


def test_built_in_manager_can_edit_rota_and_backfill_restores_it(client, db, auth_headers):
    roles = client.get("/api/v1/hr/admin/roles", headers=auth_headers).json()
    manager = next(r for r in roles if r["role_name"] == "Manager")
    assert "rota:edit" in manager["permissions"]
    assert "rota:delete" not in manager["permissions"]

    # A company created before rota:edit was added to Manager gets it at the one-time startup upgrade...
    perm = db.query(Permission).filter(Permission.module == "rota", Permission.permission_name == "edit").one()
    links = lambda: db.query(RolePermission).filter(RolePermission.role_id == manager["id"], RolePermission.permission_id == perm.id)
    links().delete()
    db.execute(text("DELETE FROM schema_flags WHERE name = :n"), {"n": _DEFAULT_ROLES_FLAG})
    db.commit()
    backfill_default_role_permissions(db)
    assert links().count() == 1
    # ...but once it has run, an admin removing it is respected on later restarts.
    links().delete()
    db.commit()
    backfill_default_role_permissions(db)
    assert links().count() == 0
    db.add(RolePermission(role_id=manager["id"], permission_id=perm.id))
    db.commit()


def test_payroll_runs_by_role(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    period = "2011-03"

    viewer = _login_as(client, db, auth_headers, cid, _tag(), [], ["payroll:view"])
    assert client.post("/api/v1/payroll/generate", headers=viewer, json={"period": period}).status_code == 403

    scoped = _login_as(client, db, auth_headers, cid, _tag(), ["ScopeDeptA"], ["payroll:view", "payroll:run_payroll"])
    r = client.post("/api/v1/payroll/generate", headers=scoped, json={"period": period})
    assert r.status_code == 403 and "department" in r.json()["detail"]

    officer = _login_as(client, db, auth_headers, cid, _tag(), [], ["payroll:view", "payroll:run_payroll"])
    r = client.post("/api/v1/payroll/generate", headers=officer, json={"period": period})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    assert client.post(f"/api/v1/payroll/runs/{run_id}/approve", headers=officer).status_code == 200
    assert client.post(f"/api/v1/payroll/runs/{run_id}/wps-batch", headers=officer).status_code == 201
    assert client.get(f"/api/v1/payroll/runs/{run_id}/sif", headers=officer).status_code == 200
