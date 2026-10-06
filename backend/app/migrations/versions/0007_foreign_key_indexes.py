"""Indexes for foreign keys that hot write paths make PostgreSQL check.

Found by the PostgreSQL load test: deleting one invoice's journal lines took 6.4 s,
because PostgreSQL checks general_ledger_entries.journal_line_id for every deleted line
and that column had no index (a full scan of 1.8M GL rows each time). The same applies,
less often, to bank_reconciliation_matches.ledger_entry_id (GL rows deleted on re-post),
audit_logs.employee_id (employee deletes) and audit_log_details.audit_log_id.

Revision ID: 0007_foreign_key_indexes
Revises: 0006_account_period_totals
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_foreign_key_indexes"
down_revision = "0006_account_period_totals"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_gl_entries_journal_line_id", "general_ledger_entries", ["journal_line_id"]),
    ("ix_bank_recon_matches_ledger_entry_id", "bank_reconciliation_matches", ["ledger_entry_id"]),
    ("ix_audit_logs_employee_id", "audit_logs", ["employee_id"]),
    ("ix_audit_log_details_audit_log_id", "audit_log_details", ["audit_log_id"]),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, cols in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _cols in _INDEXES:
        op.drop_index(name, table_name=table)
