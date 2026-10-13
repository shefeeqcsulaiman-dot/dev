"""The SQL employees table becomes the main copy of HRMS employee records (step C).

Adds employees.extra (an HRMS record's fields without a column, the photo aside), fills it
for every employee record that has its row, and writes the row's values (name, department,
designation, salary, allowances, IBAN, branch) back into the JSON (app/employee_records.py),
so the JSON shows what payroll uses -- e.g. a salary a role without salary access had
saved as 0 in the JSON while the row kept the real one.

Revision ID: 0030_employees_main_copy
Revises: 0029_request_records
Create Date: 2026-10-13
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision = "0030_employees_main_copy"
down_revision = "0029_request_records"
branch_labels = None
depends_on = None

_BATCH = 200  # employee records can carry photos and documents
_COLUMNS = ("full_name", "department", "designation", "basic_salary", "housing_allowance", "transport_allowance",
            "other_allowance", "iban", "branch_id")


def upgrade() -> None:
    from app.employee_records import COLLECTION, canonical_employee, employee_extra, employee_no

    bind = op.get_bind()
    if "extra" not in {c["name"] for c in sa.inspect(bind).get_columns("employees")}:
        op.add_column("employees", sa.Column("extra", sa.Text(), nullable=True))

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("company_id", sa.String),
                       sa.column("collection", sa.String), sa.column("payload", sa.Text))
    employees = sa.table("employees", sa.column("id", sa.String), sa.column("company_id", sa.String),
                         sa.column("employee_no", sa.String), sa.column("extra", sa.Text),
                         *[sa.column(c) for c in _COLUMNS])
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.company_id, records.c.payload)
                            .where(records.c.collection == COLLECTION, records.c.id > last_id)
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        for row_id, company_id, payload in rows:
            try:
                record = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            number = employee_no(record) if isinstance(record, dict) else ""
            if not number:
                continue
            emp = bind.execute(sa.select(employees).where(employees.c.company_id == company_id,
                                                          employees.c.employee_no == number)).mappings().first()
            if emp is None:
                continue
            bind.execute(sa.update(employees).where(employees.c.id == emp["id"]).values(extra=employee_extra(record)))
            stored = canonical_employee(record, SimpleNamespace(**{c: emp[c] for c in _COLUMNS}))
            if stored != record:
                bind.execute(sa.update(records).where(records.c.id == row_id)
                             .values(payload=json.dumps(stored, ensure_ascii=False, default=str)))
        last_id = rows[-1][0]


def downgrade() -> None:
    op.drop_column("employees", "extra")
