"""Step D for tasks: their JSON is no longer stored (app/record_payloads.py).

Refills task_records first (rows now keep empty text as "" so they rebuild the JSON
exactly), then empties each task's stored JSON -- only where its row rebuilds exactly the
same JSON; downgrade stores it again.

Revision ID: 0037_task_json_in_table
Revises: 0036_request_json_in_table
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0037_task_json_in_table"
down_revision = "0036_request_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "title", "department", "assigned_to", "assigned_to_name",
            "status", "is_open", "priority", "progress", "due_date", "repeat", "extra")
_BATCH = 500


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("task_records", *[sa.column(c, sa.Boolean()) if c == "is_open"
                                      else sa.column(c, sa.Integer()) if c == "progress"
                                      else sa.column(c) for c in _COLUMNS])
    return records, rows


def _rebuild(row) -> dict:
    from app.main_copy import record_from
    from app.task_records import FIELDS

    return record_from(dict(row), FIELDS)


def upgrade() -> None:
    from app.task_records import mirror

    bind = op.get_bind()
    records, rows_table = _tables()
    bind.execute(sa.text("DELETE FROM task_records"))
    mirror.refill_all(bind, rows_table)
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection == "tasks", records.c.id > last_id, records.c.payload != "")
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
