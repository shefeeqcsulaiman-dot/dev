"""Employees: the SQL employees table is the main copy of each HRMS employee record.

An HRMS employee is saved as JSON app-data ("employees") and synced into the SQL employees
row that payroll, ESS logins and attendance read (routers/app_data.py sync_domain_model:
salary and allowances only change for a role allowed to see salary, IBAN is checked,
status becomes active / inactive). That row also holds what HRMS never sends (login, role,
last login). Step C makes it the main copy: after the sync, the row's fields are written
back into the JSON (name, department, designation, salary, allowances, IBAN, branch), and
the record's other fields (documents, visa and passport dates, contacts...) are kept in
employees.extra, so the row holds the whole employee. The JSON can then no longer disagree
with what payroll uses -- e.g. a role without salary access saving a department change used
to store salary 0 in the JSON while the row kept the real salary.

Not written back: status (the JSON keeps HRMS's wording, "On Leave" etc.; the row keeps
active / inactive) and the photo (the row has it in its own column, so it isn't copied into
extra unless the record's differs). Check or rewrite with
`python -m app.employee_records [--rewrite-json]`.

Step D: the JSON isn't stored (app/record_payloads.py); employee_record() rebuilds it from
the row. extra["~keys"] lists which of each column's field names the record uses ("name" or
"full_name", ...), so the rebuilt record has exactly the record's keys.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import AppDataRecord, Employee

COLLECTION = "employees"
# JSON field(s) -> employees column. The first JSON key the record uses is written back.
MAPPED = (
    (("name", "full_name"), "full_name"),
    (("department",), "department"),
    (("designation", "position"), "designation"),
    (("salary", "basic_salary"), "basic_salary"),
    (("housing_allowance",), "housing_allowance"),
    (("transport_allowance",), "transport_allowance"),
    (("other_allowance",), "other_allowance"),
    (("iban",), "iban"),
    (("branch_id",), "branch_id"),
)
_NOT_EXTRA = {key for keys, _ in MAPPED for key in keys} | {"photo"}
# extra's list of the column field names (and "photo") the record carries.
_KEYS = "~keys"


def employee_no(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("employee_no") or record.get("emp_no") or "").strip()


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value if value is not None else ""


def employee_extra(record: dict[str, Any], emp: Employee | None = None) -> str | None:
    """The record's fields the employees row has no column for (the photo only when it
    differs from the row's), and which column field names it uses (_KEYS)."""
    extra = {k: v for k, v in record.items() if k not in _NOT_EXTRA}
    carried = []
    for keys, _column in MAPPED:
        present = [key for key in keys if key in record]
        carried += present[:1]
        extra.update({key: record[key] for key in present[1:]})  # a second name for the column: as given
    if "photo" in record:
        carried.append("photo")
        if emp is not None and record["photo"] != (emp.photo or ""):
            extra["photo"] = record["photo"]
    if carried:
        extra[_KEYS] = carried
    return json.dumps(extra, ensure_ascii=False, default=str) if extra else None


def employee_record(emp: Any) -> dict[str, Any]:
    """The JSON record rebuilt from its row alone ({} for a row saved before _KEYS)."""
    try:
        extra = json.loads(emp.extra or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(extra, dict) or _KEYS not in extra:
        return {}
    carried = extra.pop(_KEYS)
    out = extra
    for keys, column in MAPPED:
        key = next((k for k in keys if k in carried), None)
        if key:
            out[key] = _json_value(getattr(emp, column))
    if "photo" in carried and "photo" not in out:
        out["photo"] = emp.photo or ""
    return out


def typed_rows(session: Session, records: list[AppDataRecord]) -> dict[str, Employee]:
    """{record id: its employees row}, found by (company, employee number = record key)."""
    wanted: dict[str, set[str]] = {}
    for rec in records:
        number = str(rec.record_key or "").strip()
        if number:
            wanted.setdefault(rec.company_id, set()).add(number)
    found = {}
    for company_id, numbers in wanted.items():
        ordered = sorted(numbers)
        for start in range(0, len(ordered), 500):
            for emp in session.query(Employee).filter(Employee.company_id == company_id,
                                                      Employee.employee_no.in_(ordered[start:start + 500])):
                found[(company_id, emp.employee_no)] = emp
    out = {}
    for rec in records:
        emp = found.get((rec.company_id, str(rec.record_key or "").strip()))
        if emp is not None:
            out[rec.id] = emp
    return out


def canonical_employee(record: dict[str, Any], emp: Employee) -> dict[str, Any]:
    """The JSON record with its row's values written back into the fields it uses."""
    out = dict(record)
    for keys, column in MAPPED:
        present = [key for key in keys if key in out]
        if not present and column != "full_name":
            continue
        out[present[0] if present else keys[0]] = _json_value(getattr(emp, column))
    return out


def _row(db: Session, company_id: str, record: dict[str, Any]) -> Employee | None:
    number = employee_no(record)
    if not number:
        return None
    return db.query(Employee).filter(Employee.company_id == company_id, Employee.employee_no == number).first()


def store_main_copy(db: Session, company_id: str, app_record: AppDataRecord) -> None:
    """After an HRMS save has been synced into the employees row: keep the record's other
    fields on the row and regenerate the JSON from the row."""
    try:
        record = json.loads(app_record.payload or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(record, dict):
        return
    db.flush()  # the sync may have just added the row; sessions here don't autoflush
    emp = _row(db, company_id, record)
    if emp is None:
        return
    stored = canonical_employee(record, emp)
    emp.extra = employee_extra(stored, emp)
    if stored != record:
        app_record.payload = json.dumps(stored, ensure_ascii=False, default=str)
    # The row now holds the whole record: stop storing the JSON (when it rebuilds exactly).
    from app.record_payloads import blank_if_rebuilt

    db.flush()
    blank_if_rebuilt(db, [app_record])


def stale_json(db: Session, company_id: str) -> list[str]:
    """Ids of employee records whose JSON disagrees with their row (or whose extra is stale)."""
    stale = []
    for row in db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id,
                                              AppDataRecord.collection == COLLECTION):
        try:
            record = json.loads(row.payload or "{}")
        except (TypeError, ValueError):
            continue
        emp = _row(db, company_id, record) if isinstance(record, dict) else None
        if emp is not None and (canonical_employee(record, emp) != record or emp.extra != employee_extra(record, emp)):
            stale.append(row.id)
    return stale


def rewrite_json(db: Session, company_id: str, record_ids: list[str]) -> None:
    for row in db.query(AppDataRecord).filter(AppDataRecord.id.in_(record_ids)):
        store_main_copy(db, company_id, row)


def _main() -> None:
    import argparse

    from sqlalchemy import select

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rewrite) employee JSON records against the employees table.")
    parser.add_argument("--rewrite-json", action="store_true", help="regenerate records that disagree with their row")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection == COLLECTION).distinct())]
        stale = {c: ids for c in companies if (ids := stale_json(db, c))}
        print(f"{len(companies)} companies checked, {sum(map(len, stale.values()))} employee records out of step")
        if args.rewrite_json and stale:
            for company_id, ids in stale.items():
                rewrite_json(db, company_id, ids)
            db.commit()
            print("JSON rewritten")


if __name__ == "__main__":
    _main()
