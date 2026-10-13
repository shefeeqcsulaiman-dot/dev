"""Step D for the small lookup lists: their JSON is no longer stored (app/record_payloads.py).

Units, sales categories, service types, salespeople, shift setup, holidays and bank
accounts are rebuilt from lookup_records when read. This empties their stored JSON -- but
only for a record whose row rebuilds exactly the same JSON; any other record keeps it.

Revision ID: 0034_lookup_json_in_table
Revises: 0033_lookup_records
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0034_lookup_json_in_table"
down_revision = "0033_lookup_records"
branch_labels = None
depends_on = None

_COLLECTIONS = ("salesUnits", "salesCategories", "serviceTypes", "salesPeople", "rotaShifts", "hrHolidays", "bankAccounts")
_BATCH = 500


def _rebuild(row) -> dict:
    from app.lookup_records import FIELDS
    from app.main_copy import record_from

    return record_from(dict(row), FIELDS)


def upgrade() -> None:
    bind = op.get_bind()
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    lookups = sa.table("lookup_records", sa.column("id", sa.String), sa.column("code", sa.String),
                       sa.column("name", sa.String), sa.column("status", sa.String), sa.column("extra", sa.Text))
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(_COLLECTIONS), records.c.id > last_id, records.c.payload != "")
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        typed = {r["id"]: r for r in bind.execute(sa.select(lookups).where(lookups.c.id.in_([i for i, _ in rows])))
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
    """Store the JSON again, rebuilt from lookup_records."""
    bind = op.get_bind()
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    lookups = sa.table("lookup_records", sa.column("id", sa.String), sa.column("code", sa.String),
                       sa.column("name", sa.String), sa.column("status", sa.String), sa.column("extra", sa.Text))
    for row in bind.execute(sa.select(lookups).join(records, records.c.id == lookups.c.id)
                            .where(records.c.payload == "")).mappings().all():
        bind.execute(sa.update(records).where(records.c.id == row["id"])
                     .values(payload=json.dumps(_rebuild(row), ensure_ascii=False, default=str)))
