"""document_lines for quotations, bills and purchase records too, refilled for all.

app/doc_lines.py now also writes the lines of quotations, bills and purchase records
(and reads purchase-style fields: unit_cost, sku); this rebuilds the whole table from
the JSON records with those rules.

Revision ID: 0011_more_document_lines
Revises: 0010_document_lines
Create Date: 2026-10-08
"""
from alembic import op

from app.doc_lines import refill_all

revision = "0011_more_document_lines"
down_revision = "0010_document_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    refill_all(op.get_bind())


def downgrade() -> None:
    # Back to sales invoices only (0010's contents).
    op.get_bind().exec_driver_sql("DELETE FROM document_lines WHERE collection <> 'salesInvoices'")
