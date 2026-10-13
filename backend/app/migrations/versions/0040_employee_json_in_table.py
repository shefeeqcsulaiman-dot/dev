"""Step D for employees: their JSON is no longer stored (app/record_payloads.py).

For each HRMS employee record already as its row regenerates it (0030), employees.extra is
rewritten with the field names the record uses (and its photo when it differs from the
row's), then the stored JSON is emptied -- only where the row rebuilds exactly the same
JSON (app/employee_records.employee_record()); downgrade stores it again.

Revision ID: 0040_employee_json_in_table
Revises: 0039_party_json_in_table
Create Date: 2026-10-13
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision = "0040_employee_json_in_table"
down_revision = "0039_party_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("full_name", "department", "designation", "basic_salary", "housing_allowance", "transport_allowance",
            "other_allowance", "iban", "branch_id", "photo", "extra")
_BATCH = 200


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("company_id", sa.String),
                       sa.column("collection", sa.String), sa.column("record_key", sa.String),
                       sa.column("payload", sa.Text))
    employees = sa.table("employees", sa.column("id", sa.String), sa.column("company_id", sa.String),
                         sa.column("employee_no", sa.String),
                         *[sa.column(c, sa.Numeric(12, 2)) if c.endswith(("salary", "allowance")) else sa.column(c)
                           for c in _COLUMNS])
    return records, employees


def upgrade() -> None:
    from app.employee_records import COLLECTION, canonical_employee, employee_extra, employee_record

    bind = op.get_bind()
    records, employees = _tables()
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.company_id, records.c.record_key, records.c.payload)
                            .where(records.c.collection == COLLECTION, records.c.id > last_id, records.c.payload != "")
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        blank = []
        for row_id, company_id, key, payload in rows:
            try:
                record = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            number = str(key or "").strip()
            if not isinstance(record, dict) or not number:
                continue
            emp = bind.execute(sa.select(employees).where(employees.c.company_id == company_id,
                                                          employees.c.employee_no == number)).mappings().first()
            if emp is None:
                continue
            row = SimpleNamespace(**{c: emp[c] for c in _COLUMNS})
            if canonical_employee(record, row) != record:
                continue  # out of step with its row: left as it is
            row.extra = employee_extra(record, row)
            bind.execute(sa.update(employees).where(employees.c.id == emp["id"]).values(extra=row.extra))
            if employee_record(row) == record:
                blank.append(row_id)
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = rows[-1][0]


def downgrade() -> None:
    from app.employee_records import COLLECTION, employee_record

    bind = op.get_bind()
    records, employees = _tables()
    for row_id, company_id, key in bind.execute(
            sa.select(records.c.id, records.c.company_id, records.c.record_key)
            .where(records.c.collection == COLLECTION, records.c.payload == "")).all():
        emp = bind.execute(sa.select(employees).where(employees.c.company_id == company_id,
                                                      employees.c.employee_no == str(key or "").strip())).mappings().first()
        record = employee_record(SimpleNamespace(**{c: emp[c] for c in _COLUMNS})) if emp else {}
        bind.execute(sa.update(records).where(records.c.id == row_id)
                     .values(payload=json.dumps(record, ensure_ascii=False, default=str)))
