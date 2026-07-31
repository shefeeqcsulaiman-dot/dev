"""Regression coverage for DELETE /superadmin/companies/{id}. The cascade
manually deletes ~60 company-scoped tables in FK-safe order (no ON DELETE
CASCADE at the DB level) — Role, RolePermission, CompanyLocation,
EmployeeLocation, AttendanceSession, EmployeeLocationLog, and LeaveRequest
were added to the schema after this cascade was written and were never
added to it. Deleting any company that had used HRMS GPS attendance,
custom roles, or leave requests raised an uncaught FK IntegrityError,
surfaced to the user as a generic "An internal error occurred" — found
via a live report of exactly that error when deleting a real company."""
from datetime import UTC, datetime

from app.models import (
    AttendanceSession, Company, CompanyLocation, Employee, EmployeeLocation,
    EmployeeLocationLog, LeaveRequest, Permission, Role, RolePermission, User,
)
from app.security import hash_password


def _make_superadmin(db):
    company = Company(name="ETaxFlow Admin Test", trn="SUPERADMIN-TEST")
    db.add(company)
    db.flush()
    admin = User(company_id=company.id, email="superadmin-test@etaxflow.com",
                 full_name="Super Admin", role="superadmin", password_hash=hash_password("test12345"))
    db.add(admin)
    db.commit()
    r = _login_client_ref["client"].post("/api/v1/auth/login", json={"email": admin.email, "password": "test12345"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


_login_client_ref = {}


def test_delete_company_with_hrms_gps_and_rbac_data(client, db):
    _login_client_ref["client"] = client
    target = Company(name="Target Co", trn="TARGET-DELETE-TEST")
    db.add(target)
    db.flush()

    emp = Employee(company_id=target.id, employee_no="DEL-001", full_name="Delete Test Employee")
    db.add(emp)
    db.flush()

    role = Role(company_id=target.id, role_name="Delete Test Role")
    db.add(role)
    db.flush()
    perm = db.query(Permission).first()
    if not perm:
        perm = Permission(module="employees", permission_name="view")
        db.add(perm)
        db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=perm.id))
    emp.role_id = role.id

    location = CompanyLocation(company_id=target.id, location_name="HQ", latitude=25.2, longitude=55.3)
    db.add(location)
    db.flush()
    emp.work_location_id = location.id
    db.add(EmployeeLocation(employee_id=emp.id, location_id=location.id))

    session = AttendanceSession(company_id=target.id, employee_id=emp.id, location_id=location.id,
                                 check_in=datetime.now(UTC), status="open")
    db.add(session)
    db.flush()
    db.add(EmployeeLocationLog(company_id=target.id, employee_id=emp.id, session_id=session.id,
                                latitude=25.2, longitude=55.3))
    db.add(LeaveRequest(company_id=target.id, employee_id=emp.id, leave_type="Annual Leave",
                         start_date="2026-08-01", end_date="2026-08-02", days=2, status="pending"))
    db.commit()

    headers = _make_superadmin(db)
    r = client.delete(f"/api/v1/superadmin/companies/{target.id}", headers=headers)
    assert r.status_code == 200, r.text

    assert db.query(Company).filter(Company.id == target.id).first() is None
    assert db.query(Employee).filter(Employee.company_id == target.id).count() == 0
    assert db.query(Role).filter(Role.company_id == target.id).count() == 0
    assert db.query(CompanyLocation).filter(CompanyLocation.company_id == target.id).count() == 0
