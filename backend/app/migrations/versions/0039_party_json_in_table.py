"""Step D for customers and suppliers: their JSON is no longer stored (app/record_payloads.py).

Refills parties first, then empties each record's stored JSON -- only where its row
rebuilds exactly the same JSON; downgrade stores it again.

Revision ID: 0039_party_json_in_table
Revises: 0038_rota_json_in_table
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0039_party_json_in_table"
down_revision = "0038_rota_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "kind", "record_key", "name", "name_key", "trn", "email", "phone", "address",
            "emirate", "category", "contact", "credit_limit", "status", "extra")
_BATCH = 500


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("parties", *[sa.column(c, sa.Numeric(14, 2)) if c == "credit_limit"
                                 else sa.column(c, sa.String()) for c in _COLUMNS])
    return records, rows


def _rebuild(row) -> dict:
    from app.parties import party_record

    return party_record(dict(row))


def upgrade() -> None:
    from app.parties import mirror

    bind = op.get_bind()
    records, rows_table = _tables()
    bind.execute(sa.text("DELETE FROM parties"))
    mirror.refill_all(bind, rows_table)
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(("customers", "vendors")), records.c.id > last_id,
                                   records.c.payload != "")
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
