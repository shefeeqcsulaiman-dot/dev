"""rota_records becomes the main copy of rota assignments (step C, like 0024-0026).

Adds rota_records.extra (an assignment's fields without a column of their own), so a row
holds the whole assignment; rewrites each assignment's JSON as its row regenerates it
(app/rota_records.py canonical_rota(): date YYYY-MM-DD, break_minutes a number, text
unchanged); and refills the table. From here on models.py applies canonical_rota() to every
rota write.

Revision ID: 0027_rota_main_copy
Revises: 0026_tasks_main_copy
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0027_rota_main_copy"
down_revision = "0026_tasks_main_copy"
branch_labels = None
depends_on = None

_BATCH = 1000
_COLUMNS = ("id", "company_id", "branch_id", "record_key", "employee_no", "employee_name", "department", "location",
            "day", "kind", "code", "shift_type", "start_time", "end_time", "break_minutes", "status", "extra")


def upgrade() -> None:
    from app.rota_records import COLLECTIONS, canonical_rota, mirror

    bind = op.get_bind()
    if "extra" not in {c["name"] for c in sa.inspect(bind).get_columns("rota_records")}:
        op.add_column("rota_records", sa.Column("extra", sa.Text(), nullable=True))

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
                canonical = canonical_rota(doc)
                if canonical != doc:
                    values.append({"_id": row_id, "doc": json.dumps(canonical, ensure_ascii=False, default=str)})
        if values:
            bind.execute(update, values)
        last_id = rows[-1][0]

    bind.execute(sa.text("DELETE FROM rota_records"))
    table = sa.table("rota_records", *[sa.column(c, sa.Float()) if c == "break_minutes" else sa.column(c)
                                       for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_column("rota_records", "extra")
