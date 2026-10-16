"""source_transactions.document_date and invoices.invoice_date: a posting is dated by its
document's own date (a September invoice typed in October belongs to September's ledger,
P&L and VAT return), not by the day it was saved.

Revision ID: 0045_document_dates
Revises: 0044_user_last_activity
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0045_document_dates"
down_revision = "0044_user_last_activity"
branch_labels = None
depends_on = None

_COLUMNS = (("source_transactions", "document_date"), ("invoices", "invoice_date"))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column in _COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(column, sa.Date(), nullable=True))


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.drop_column(table, column)
