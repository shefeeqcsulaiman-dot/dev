"""Due date column on app_data_records (due_date), filled for existing documents.

Sales invoices, bills and purchases keep their due date inside the JSON, so "overdue" and
the Bills page's "due in the next 7 days" decoded every open document on each request.
app/doc_index.py doc_due_date() now stamps it on every save; this adds the column and
index and fills it for what is already stored, with the same rule.

Revision ID: 0021_document_due_date
Revises: 0020_rota_records
Create Date: 2026-10-12
"""
import json

import sqlalchemy as sa
from alembic import op

from app.doc_index import DUE_FIELDS, doc_due_date

revision = "0021_document_due_date"
down_revision = "0020_rota_records"
branch_labels = None
depends_on = None

_BATCH = 1000
_INDEX = "ix_app_data_company_collection_due"


def _load(payload):
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "due_date" not in {c["name"] for c in inspector.get_columns("app_data_records")}:
        op.add_column("app_data_records", sa.Column("due_date", sa.String(10), nullable=True))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("app_data_records")}:
        op.create_index(_INDEX, "app_data_records", ["company_id", "collection", "due_date"])

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text), sa.column("due_date", sa.String))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(due_date=sa.bindparam("due"))
    for collection in sorted(DUE_FIELDS):
        last_id = ""
        while True:
            rows = bind.execute(
                sa.select(records.c.id, records.c.payload)
                .where(records.c.collection == collection, records.c.id > last_id)
                .order_by(records.c.id).limit(_BATCH)
            ).all()
            if not rows:
                break
            values = [{"_id": row_id, "due": doc_due_date(_load(payload), collection)} for row_id, payload in rows]
            values = [v for v in values if v["due"]]
            if values:
                bind.execute(update, values)
            last_id = rows[-1][0]


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="app_data_records")
    op.drop_column("app_data_records", "due_date")
