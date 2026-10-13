"""document_records also holds bills, quotations, expenses and payments (step C).

Rewrites those documents' JSON as their row regenerates it (app/document_records.py:
amounts numbers at full precision, text and dates unchanged, lines kept) and refills the
table for every document type it covers.

Revision ID: 0031_more_document_records
Revises: 0030_employees_main_copy
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0031_more_document_records"
down_revision = "0030_employees_main_copy"
branch_labels = None
depends_on = None

_BATCH = 500
_NEW = ("bills", "quotations", "expenses", "payments")
_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "number", "party", "party_trn", "doc_date",
            "due_date", "status", "subtotal", "vat", "total", "paid", "salesperson", "location", "extra")
_AMOUNTS = {"subtotal", "vat", "total", "paid"}


def upgrade() -> None:
    from app.document_records import canonical_document, mirror

    bind = op.get_bind()
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(payload=sa.bindparam("doc"))
    for collection in _NEW:
        last_id = ""
        while True:
            rows = bind.execute(sa.select(records.c.id, records.c.payload)
                                .where(records.c.collection == collection, records.c.id > last_id)
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
                    canonical = canonical_document(collection, doc)
                    if canonical != doc:
                        values.append({"_id": row_id, "doc": json.dumps(canonical, ensure_ascii=False, default=str)})
            if values:
                bind.execute(update, values)
            last_id = rows[-1][0]

    bind.execute(sa.text("DELETE FROM document_records"))
    table = sa.table("document_records", *[sa.column(c, sa.Numeric(18, 4)) if c in _AMOUNTS else sa.column(c)
                                           for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    sa_table = sa.table("document_records", sa.column("collection", sa.String))
    op.get_bind().execute(sa.delete(sa_table).where(sa_table.c.collection.in_(_NEW)))
