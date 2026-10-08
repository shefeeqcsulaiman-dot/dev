"""Line items of sales invoices as rows (document_lines), filled for existing invoices.

"Invoices containing this product" used to search every sales invoice's JSON text.
app/doc_lines.py now writes each invoice's lines to document_lines on every save; this
creates the table and fills it for what is already stored (5,000 invoices: ~0.6 s).

Revision ID: 0010_document_lines
Revises: 0009_report_figures
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

from app.doc_lines import LINE_COLLECTIONS, line_rows

revision = "0010_document_lines"
down_revision = "0009_report_figures"
branch_labels = None
depends_on = None

_BATCH = 1000


def upgrade() -> None:
    bind = op.get_bind()
    if "document_lines" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "document_lines",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("record_id", sa.String(36), nullable=False),
            sa.Column("collection", sa.String(80), nullable=False),
            sa.Column("line_no", sa.Integer, nullable=False),
            sa.Column("doc_date", sa.String(40)),
            sa.Column("doc_ref", sa.String(160)),
            sa.Column("doc_source", sa.String(40)),
            sa.Column("item_name", sa.String(255)),
            sa.Column("description_key", sa.String(255)),
            sa.Column("product_name_key", sa.String(255)),
            sa.Column("product_key", sa.String(255)),
            sa.Column("product_code", sa.String(80)),
            sa.Column("unit", sa.String(40)),
            sa.Column("quantity", sa.Numeric(18, 6)),
            sa.Column("unit_price", sa.Numeric(14, 2)),
            sa.Column("line_total", sa.Numeric(14, 2)),
        )
        op.create_index("ix_document_lines_record", "document_lines", ["record_id"])
        op.create_index("ix_document_lines_company_collection", "document_lines", ["company_id", "collection"])

    # Typed columns, so Decimal values bind on every database (SQLite needs the type).
    table = sa.table(
        "document_lines",
        *(sa.column(c) for c in (
            "company_id", "record_id", "collection", "line_no", "doc_date", "doc_ref", "doc_source", "item_name",
            "description_key", "product_name_key", "product_key", "product_code", "unit")),
        sa.column("quantity", sa.Numeric(18, 6)),
        sa.column("unit_price", sa.Numeric(14, 2)),
        sa.column("line_total", sa.Numeric(14, 2)),
    )
    records = sa.table("app_data_records", sa.column("id"), sa.column("company_id"),
                       sa.column("collection"), sa.column("payload"))
    bind.execute(sa.delete(table))
    last_id = ""
    while True:  # keyset batches: memory stays flat however many invoices there are
        batch = bind.execute(
            sa.select(records.c.id, records.c.company_id, records.c.collection, records.c.payload)
            .where(records.c.collection.in_(LINE_COLLECTIONS), records.c.id > last_id)
            .order_by(records.c.id)
            .limit(_BATCH)
        ).all()
        if not batch:
            break
        rows = [row for rid, cid, coll, payload in batch for row in line_rows(cid, rid, coll, payload)]
        if rows:
            bind.execute(sa.insert(table), rows)
        last_id = batch[-1][0]


def downgrade() -> None:
    op.drop_table("document_lines")
