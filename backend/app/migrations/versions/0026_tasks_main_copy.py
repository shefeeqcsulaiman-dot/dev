"""task_records becomes the main copy of tasks (step C, like 0024/0025).

Adds task_records.extra (a task's fields without a column of their own), so a row holds the
whole task; rewrites each task's JSON as its row regenerates it (app/task_records.py
canonical_task(): progress a whole number 0-100, due_date YYYY-MM-DD, text unchanged); and
refills the table. From here on models.py applies canonical_task() to every task write.

Revision ID: 0026_tasks_main_copy
Revises: 0025_products_main_copy
Create Date: 2026-10-12
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0026_tasks_main_copy"
down_revision = "0025_products_main_copy"
branch_labels = None
depends_on = None

_BATCH = 1000
_COLUMNS = ("id", "company_id", "branch_id", "record_key", "title", "department", "assigned_to", "assigned_to_name",
            "status", "is_open", "priority", "progress", "due_date", "repeat", "extra")


def upgrade() -> None:
    from app.task_records import COLLECTIONS, canonical_task, mirror

    bind = op.get_bind()
    if "extra" not in {c["name"] for c in sa.inspect(bind).get_columns("task_records")}:
        op.add_column("task_records", sa.Column("extra", sa.Text(), nullable=True))

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(payload=sa.bindparam("doc"))
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(COLLECTIONS), records.c.id > last_id)
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        values = []
        for row_id, payload in rows:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(doc, dict):
                canonical = canonical_task(doc)
                if canonical != doc:
                    values.append({"_id": row_id, "doc": json.dumps(canonical, ensure_ascii=False, default=str)})
        if values:
            bind.execute(update, values)
        last_id = rows[-1][0]

    bind.execute(sa.text("DELETE FROM task_records"))
    table = sa.table("task_records", *[sa.column(c, sa.Boolean()) if c == "is_open"
                                       else sa.column(c, sa.Integer()) if c == "progress"
                                       else sa.column(c) for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_column("task_records", "extra")
