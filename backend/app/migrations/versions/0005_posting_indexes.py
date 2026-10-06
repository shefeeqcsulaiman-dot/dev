"""Indexes for the posting tables, found missing by the PostgreSQL load test.

Saving an invoice or purchase replaces its source_transaction_lines by source_id, and
reversals/deletes remove posting_jobs by source_id and GL rows by journal_entry_id;
none of those columns were indexed, so each save scanned the whole table (about
700 ms per save at 1M lines). upsert_source_transaction() also looks a posting up by
(company, module, reference).

Revision ID: 0005_posting_indexes
Revises: 0004_expense_index
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0005_posting_indexes"
down_revision = "0004_expense_index"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_source_tx_lines_source_id", "source_transaction_lines", ["source_id"]),
    ("ix_posting_jobs_source_id", "posting_jobs", ["source_id"]),
    ("ix_gl_entries_journal_entry_id", "general_ledger_entries", ["journal_entry_id"]),
    ("ix_source_tx_company_module_ref", "source_transactions", ["company_id", "module", "reference"]),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, cols in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _cols in _INDEXES:
        op.drop_index(name, table_name=table)
