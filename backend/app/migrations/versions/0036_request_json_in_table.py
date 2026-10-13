"""Step D for employee requests and HR workflow lists: their JSON is no longer stored.

Overtime, loans, salary advances, attendance corrections, task requests, payroll
adjustments, rota swaps / approvals, job requisitions and candidates are rebuilt from
request_records when read (app/record_payloads.py). This empties their stored JSON -- only
for a record whose row rebuilds exactly the same JSON (e.g. a request dated "13/10/2026" keeps
its JSON, as its row holds the date as 2026-10-13); downgrade stores it again.

Revision ID: 0036_request_json_in_table
Revises: 0035_company_branch_limit
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0036_request_json_in_table"
down_revision = "0035_company_branch_limit"
branch_labels = None
depends_on = None

_COLLECTIONS = ("overtimeRequests", "employeeLoans", "salaryAdvances", "attendanceCorrections", "taskRequests",
                "payrollAdjustments", "rotaSwaps", "rotaApprovals", "jobRequisitions", "candidates")
_COLUMNS = ("id", "employee_no", "employee_name", "department", "status", "request_date", "amount", "hours", "months",
            "reason", "title", "extra")
_BATCH = 500


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("request_records", *[sa.column(c, sa.Numeric(18, 4)) if c in ("amount", "hours", "months")
                                         else sa.column(c) for c in _COLUMNS])
    return records, rows


def _rebuild(row) -> dict:
    from app.main_copy import record_from
    from app.request_records import FIELDS

    return record_from(dict(row), FIELDS)


def upgrade() -> None:
    bind = op.get_bind()
    records, rows_table = _tables()
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(_COLLECTIONS), records.c.id > last_id, records.c.payload != "")
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        typed = {r["id"]: r for r in bind.execute(sa.select(rows_table).where(rows_table.c.id.in_([i for i, _ in rows])))
                 .mappings()}
        blank = []
        for row_id, payload in rows:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            row = typed.get(row_id)
            if row is not None and isinstance(doc, dict) and _rebuild(row) == doc:
                blank.append(row_id)
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = rows[-1][0]


def downgrade() -> None:
    bind = op.get_bind()
    records, rows_table = _tables()
    for row in bind.execute(sa.select(rows_table).join(records, records.c.id == rows_table.c.id)
                            .where(records.c.payload == "")).mappings().all():
        bind.execute(sa.update(records).where(records.c.id == row["id"])
                     .values(payload=json.dumps(_rebuild(row), ensure_ascii=False, default=str)))
