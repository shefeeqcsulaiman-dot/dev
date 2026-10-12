"""doc_open on app_data_records: money still owed on a sales invoice, bill or purchase.

"Open" (more than 0.01 left after payments and not in a settled status) was worked out per
row on every request, so Overdue / Open lists and the payment screen's allocation list
checked every candidate document. app/doc_index.py now keeps it as a column (on save, and
in recompute_paid() whenever payments move); this adds it with an index for the overdue
lookup and fills it for existing documents with the same rule, in SQL.

Revision ID: 0023_document_open_flag
Revises: 0022_open_work_status
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

from app.doc_index import OPEN_FLAG_COLLECTIONS, PAID_TOLERANCE, SETTLED_STATUS_WORDS

revision = "0023_document_open_flag"
down_revision = "0022_open_work_status"
branch_labels = None
depends_on = None

_INDEX = "ix_app_data_company_collection_open_due"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "doc_open" not in {c["name"] for c in inspector.get_columns("app_data_records")}:
        op.add_column("app_data_records", sa.Column("doc_open", sa.Boolean(), nullable=True))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("app_data_records")}:
        op.create_index(_INDEX, "app_data_records", ["company_id", "collection", "doc_open", "due_date"])

    records = sa.table("app_data_records", sa.column("collection", sa.String), sa.column("doc_status", sa.String),
                       sa.column("amount", sa.Numeric(14, 2)), sa.column("amount_paid", sa.Numeric(14, 2)),
                       sa.column("doc_open", sa.Boolean))
    status = sa.func.coalesce(records.c.doc_status, "")
    is_open = sa.and_(
        sa.func.coalesce(records.c.amount, 0) - sa.func.coalesce(records.c.amount_paid, 0) > PAID_TOLERANCE,
        *[~status.like(f"%{word}%") for word in SETTLED_STATUS_WORDS],
    )
    bind.execute(sa.update(records).where(records.c.collection.in_(sorted(OPEN_FLAG_COLLECTIONS)))
                 .values(doc_open=sa.case((is_open, sa.true()), else_=sa.false())))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="app_data_records")
    op.drop_column("app_data_records", "doc_open")
