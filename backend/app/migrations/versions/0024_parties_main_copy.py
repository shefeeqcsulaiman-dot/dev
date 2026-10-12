"""parties becomes the main copy of customers and suppliers (step C).

Adds parties.extra (the record's fields without a column of their own), so a row holds the
whole record; refills the table with the new rules (a field present but empty is kept as
""); and rewrites each customer / supplier JSON record as its row regenerates it
(app/parties.py canonical_party(): TRN digits only, credit limit a number, same keys).

Revision ID: 0024_parties_main_copy
Revises: 0023_document_open_flag
Create Date: 2026-10-12
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0024_parties_main_copy"
down_revision = "0023_document_open_flag"
branch_labels = None
depends_on = None

_BATCH = 1000
_COLUMNS = ("id", "company_id", "branch_id", "kind", "record_key", "name", "name_key", "trn", "email", "phone",
            "address", "emirate", "category", "contact", "credit_limit", "status", "extra")


def upgrade() -> None:
    from app.parties import KINDS, canonical_party, mirror

    bind = op.get_bind()
    if "extra" not in {c["name"] for c in sa.inspect(bind).get_columns("parties")}:
        op.add_column("parties", sa.Column("extra", sa.Text(), nullable=True))

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(payload=sa.bindparam("doc"))
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(sorted(KINDS)), records.c.id > last_id)
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
                canonical = canonical_party(doc)
                if canonical != doc:
                    values.append({"_id": row_id, "doc": json.dumps(canonical, ensure_ascii=False, default=str)})
        if values:
            bind.execute(update, values)
        last_id = rows[-1][0]

    bind.execute(sa.text("DELETE FROM parties"))
    table = sa.table("parties", *[sa.column(c, sa.Numeric(14, 2)) if c == "credit_limit" else sa.column(c)
                                  for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_column("parties", "extra")
