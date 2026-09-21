"""Department scoping for HRMS logins.

A Role can carry a list of departments (Role.department_scope, edited in the
Add/Edit Custom Role modal). An Employee whose role has departments ticked is
*department-scoped*: every HRMS list they can open, and every record they can
create/change/delete, is limited to employees in those departments. An empty
list means "not scoped" -- company-wide, exactly as before this existed -- and
User (admin) / Branch principals are never scoped.

Employees are referenced inconsistently across the data model, so this module
resolves a department through whichever reference a record carries:

  * SQL rows (leave requests, punches, payroll items) -> Employee.id / employee_no
  * tasks.assigned_to                                  -> Employee.id
  * rota / overtime / loans / corrections `employee_id` -> Employee.employee_no
  * older records that only stored a display name       -> full_name (must be
    unambiguous: every employee with that name has to be in scope)

Anything that cannot be resolved to an in-scope employee is treated as OUT of
scope (fail closed) -- except tasks with nobody assigned yet, which stay visible
so a scoped manager can pick them up and assign them.
"""
from __future__ import annotations

from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Query, Session

from app.auth_principal import Principal, _role_department_scope
from app.models import Employee, Role

# Collections whose rows belong to a specific employee (resolved via the refs
# in _EMPLOYEE_REF_FIELDS / _EMPLOYEE_NAME_FIELDS below).
EMPLOYEE_KEYED_COLLECTIONS = frozenset({
    "leaveRequests", "attendanceCorrections", "overtimeRequests",
    "employeeLoans", "salaryAdvances", "rotaAssignments", "payrollAdjustments",
})
# Rota drafts/approvals describe a whole department's rota, not one person.
DEPARTMENT_KEYED_COLLECTIONS = frozenset({"rotaDrafts", "rotaApprovals"})
# Company-wide payroll run records (period totals, SIF files): hidden from, and
# not writable by, a department-scoped login.
COMPANY_WIDE_BLOCKED_COLLECTIONS = frozenset({"payrollRuns"})

# Shift definitions (Rota > Shift Setup) carry a list of departments they apply
# to; an empty list means "every department" (all shifts created before this
# existed). See _shift_departments().
SHIFT_COLLECTIONS = frozenset({"rotaShifts"})

SCOPED_COLLECTIONS = frozenset(
    EMPLOYEE_KEYED_COLLECTIONS | DEPARTMENT_KEYED_COLLECTIONS | COMPANY_WIDE_BLOCKED_COLLECTIONS
    | SHIFT_COLLECTIONS | {"employees", "tasks", "rotaSwaps"}
)

_EMPLOYEE_REF_FIELDS = ("employee_id", "emp_id", "employee_no")
_EMPLOYEE_NAME_FIELDS = ("employee", "employee_name")
_ALL_DEPARTMENTS = {"", "all departments", "all"}


def dept_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _shift_departments(record: dict[str, Any]) -> set[str]:
    """Departments a shift applies to (lower-cased). Accepts a list or a
    comma-separated string, and the older single `department` field. Empty
    set = the shift applies to every department."""
    raw = record.get("departments")
    if raw is None or raw == "":
        raw = record.get("department")
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, (list, tuple, set)):
        return set()
    names = {dept_key(d) for d in raw}
    names.discard("")
    names -= _ALL_DEPARTMENTS
    return names


class EmployeeDeptIndex:
    """One query per request: every employee of the company (active or not --
    a former employee's old records still belong to their department)."""

    def __init__(self, rows: Iterable[tuple[str, str, str, str]]):
        self.by_ref: dict[str, str] = {}
        self.by_name: dict[str, set[str]] = {}
        for emp_id, emp_no, full_name, department in rows:
            dept = dept_key(department)
            self.by_ref[dept_key(emp_id)] = dept
            self.by_ref[dept_key(emp_no)] = dept
            self.by_name.setdefault(dept_key(full_name), set()).add(dept)

    def department_of_ref(self, ref: Any) -> str | None:
        return self.by_ref.get(dept_key(ref))

    def departments_of_name(self, name: Any) -> set[str]:
        return self.by_name.get(dept_key(name), set())


def build_index(db: Session, company_id: str) -> EmployeeDeptIndex:
    rows = db.query(Employee.id, Employee.employee_no, Employee.full_name, Employee.department).filter(
        Employee.company_id == company_id,
    ).all()
    return EmployeeDeptIndex((r[0], r[1], r[2], r[3]) for r in rows)


def _ref_in_scope(principal: Principal, index: EmployeeDeptIndex, refs: list[Any], names: list[Any]) -> bool:
    """`refs` win over `names`; an unresolvable reference falls back to the
    name, and an unresolvable name is out of scope."""
    for ref in refs:
        if dept_key(ref):
            dept = index.department_of_ref(ref)
            if dept is not None:
                return dept in principal.department_scope
    for name in names:
        if dept_key(name):
            depts = index.departments_of_name(name)
            return bool(depts) and all(d in principal.department_scope for d in depts)
    return False


def record_in_scope(principal: Principal, index: EmployeeDeptIndex, collection: str, record: dict[str, Any]) -> bool:
    """Is this app-data record visible/writable to a department-scoped login?
    Not-scoped principals and unrelated collections are always True."""
    if not principal.is_dept_scoped or collection not in SCOPED_COLLECTIONS:
        return True
    if not isinstance(record, dict):
        return False
    scope = principal.department_scope
    if collection in COMPANY_WIDE_BLOCKED_COLLECTIONS:
        return False
    if collection == "employees":
        dept = dept_key(record.get("department"))
        if not dept:
            found = index.department_of_ref(record.get("id") or record.get("employee_no"))
            return found in scope if found is not None else False
        return dept in scope
    if collection == "tasks":
        assigned = record.get("assigned_to")
        if not dept_key(assigned):
            return True  # nobody assigned yet -> stays visible to every scoped login
        return _ref_in_scope(principal, index, [assigned], [record.get("assigned_to_name")])
    if collection in DEPARTMENT_KEYED_COLLECTIONS:
        dept = dept_key(record.get("department"))
        return dept in _ALL_DEPARTMENTS or dept in scope
    if collection in SHIFT_COLLECTIONS:
        # visible when it applies to every department, or to at least one of mine
        depts = _shift_departments(record)
        return not depts or bool(depts & scope)
    if collection == "rotaSwaps":
        return (
            _ref_in_scope(principal, index, [record.get("employee_a_id")], [record.get("employee_a")])
            and _ref_in_scope(principal, index, [record.get("employee_b_id")], [record.get("employee_b")])
        )
    # EMPLOYEE_KEYED_COLLECTIONS
    return _ref_in_scope(
        principal, index,
        [record.get(k) for k in _EMPLOYEE_REF_FIELDS],
        [record.get(k) for k in _EMPLOYEE_NAME_FIELDS],
    )


def filter_records(db: Session, principal: Principal, collection: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not principal.is_dept_scoped or collection not in SCOPED_COLLECTIONS:
        return records
    if collection in COMPANY_WIDE_BLOCKED_COLLECTIONS:
        return []
    index = build_index(db, principal.company_id)
    return [r for r in records if record_in_scope(principal, index, collection, r)]


def assert_record_writable(
    db: Session, principal: Principal, collection: str,
    incoming: dict[str, Any] | None, stored: dict[str, Any] | None = None,
    index: EmployeeDeptIndex | None = None,
) -> None:
    """Server-side enforcement for save / bulk-save / delete: BOTH the incoming
    record and the record already stored under that key must be in scope, so a
    scoped login can neither create data for another department nor take over /
    edit / delete an existing record of another department."""
    if not principal.is_dept_scoped or collection not in SCOPED_COLLECTIONS:
        return
    if collection in COMPANY_WIDE_BLOCKED_COLLECTIONS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Company-wide payroll records can't be changed by a department-scoped login",
        )
    if collection in SHIFT_COLLECTIONS:
        # A shift is shared by every department it lists, so a scoped login may
        # only create / edit / delete shifts that belong ONLY to its own
        # departments -- never an all-departments shift or one shared with a
        # department it doesn't have.
        scope = principal.department_scope
        if incoming is not None:
            depts = _shift_departments(incoming)
            if not depts or not depts <= scope:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="A shift must be limited to your own departments -- pick at least one, and only departments you manage",
                )
        if stored is not None:
            depts = _shift_departments(stored)
            if not depts or not depts <= scope:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This shift applies to other departments too, so only an administrator can change it",
                )
        return
    idx = index or build_index(db, principal.company_id)
    for rec in (incoming, stored):
        if rec is not None and not record_in_scope(principal, idx, collection, rec):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Outside your department scope")


# ── SQL helpers (Employee rows) ──────────────────────────────────────────────

def employee_in_scope(principal: Principal, employee: Employee | None) -> bool:
    if not principal.is_dept_scoped:
        return True
    return employee is not None and dept_key(employee.department) in principal.department_scope


def assert_employee_in_scope(principal: Principal, employee: Employee | None) -> None:
    """404 (not 403), matching leave.py's branch check -- don't reveal that an
    out-of-scope employee exists."""
    if not employee_in_scope(principal, employee):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")


def scope_employee_query(query: Query, principal: Principal) -> Query:
    """Restrict a query that already selects from Employee."""
    if not principal.is_dept_scoped:
        return query
    return query.filter(func.lower(func.trim(Employee.department)).in_(list(principal.department_scope)))


def scoped_employee_nos(db: Session, principal: Principal) -> set[str] | None:
    """employee_no of every in-scope employee, or None when not scoped."""
    if not principal.is_dept_scoped:
        return None
    rows = scope_employee_query(
        db.query(Employee.employee_no).filter(Employee.company_id == principal.company_id), principal,
    ).all()
    return {r[0] for r in rows}


def scoped_employee_no_select(principal: Principal):
    """A `SELECT employee_no` usable inside `.in_()` -- lets a query on a table
    that only stores employee_no (attendance punches) be department-scoped
    without joining Employee (which the branch filter may already do)."""
    return select(Employee.employee_no).where(
        Employee.company_id == principal.company_id,
        func.lower(func.trim(Employee.department)).in_(list(principal.department_scope)),
    )


# ── for code paths that hold an Employee (require_permission), not a Principal ──

def employee_scope(db: Session, emp: Employee) -> frozenset[str]:
    """The department scope of an Employee login, from its role (empty = not scoped)."""
    if not emp.role_id:
        return frozenset()
    role = db.get(Role, emp.role_id)
    return frozenset(dept_key(d) for d in _role_department_scope(role))


def scope_employee_query_by_names(query: Query, scope: frozenset[str]) -> Query:
    """Like scope_employee_query() but from a plain scope set."""
    if not scope:
        return query
    return query.filter(func.lower(func.trim(Employee.department)).in_(list(scope)))


def scoped_employee_id_select(company_id: str, scope: frozenset[str]):
    """`SELECT Employee.id` of the in-scope employees, for `.in_()` on tables that store Employee.id."""
    return select(Employee.id).where(
        Employee.company_id == company_id,
        func.lower(func.trim(Employee.department)).in_(list(scope)),
    )


def assert_employee_in_names_scope(scope: frozenset[str], employee: Employee | None) -> None:
    if scope and (employee is None or dept_key(employee.department) not in scope):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
