"""document_records: the main copy of sales invoices' and purchases' headers (step C).

Creates the table (header columns plus extra; the line items stay in the JSON and in
document_lines), rewrites each sales invoice / purchase JSON as its row regenerates it
(app/document_records.py canonical_document(): amounts numbers at full precision, text and
dates unchanged, lines kept), and fills the table. From here on models.py applies it to
every write of these documents.

Revision ID: 0028_document_records
Revises: 0027_rota_main_copy
Create Date: 2026-10-13
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0028_document_records"
down_revision = "0027_rota_main_copy"
branch_labels = None
depends_on = None

_BATCH = 500  # invoices can be large (lines)
_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "number", "party", "party_trn", "doc_date",
            "due_date", "status", "subtotal", "vat", "total", "paid", "salesperson", "location", "extra")
_AMOUNTS = {"subtotal", "vat", "total", "paid"}


def upgrade() -> None:
    from app.document_records import COLLECTIONS, canonical_document, mirror

    bind = op.get_bind()
    if "document_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "document_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("collection", sa.String(80), nullable=False),
            sa.Column("record_key", sa.String(160)),
            sa.Column("number", sa.String(160)),
            sa.Column("party", sa.String(255)),
            sa.Column("party_trn", sa.String(80)),
            sa.Column("doc_date", sa.String(10)),
            sa.Column("due_date", sa.String(10)),
            sa.Column("status", sa.String(40)),
            sa.Column("subtotal", sa.Numeric(18, 4)),
            sa.Column("vat", sa.Numeric(18, 4)),
            sa.Column("total", sa.Numeric(18, 4)),
            sa.Column("paid", sa.Numeric(18, 4)),
            sa.Column("salesperson", sa.String(120)),
            sa.Column("location", sa.String(120)),
            sa.Column("extra", sa.Text()),
        )
        op.create_index("ix_document_records_company_collection_number", "document_records",
                        ["company_id", "collection", "number"])

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(payload=sa.bindparam("doc"))
    for collection in COLLECTIONS:
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
    op.drop_table("document_records")
