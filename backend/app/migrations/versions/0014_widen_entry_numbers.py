"""Widen journal entry / voucher numbers (120) and source / stock references (200).

Auto-posted entries are numbered "AUTO-<reference>" (reversals "REV-AUTO-<reference>"), and
purchase references can carry the supplier name, e.g. "INV/2026/02964 (Eurovets Veterinary
Medicines L.L.C.)". journal_entries.entry_number (40) and the voucher_no columns (60) were
shorter than that, so saving such a purchase failed with "value too long" (2026-10-10);
source_transactions.reference and stock_movements.reference (80) overflow the same way with
longer supplier names, and inventory_valuation_layers.source_id (36) stores the purchase
reference itself.
Widening a varchar is a catalogue-only change on PostgreSQL; SQLite doesn't enforce lengths.

Revision ID: 0014_widen_entry_numbers
Revises: 0013_app_data_owner_key
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_widen_entry_numbers"
down_revision = "0013_app_data_owner_key"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("journal_entries", "entry_number", 120, False),
    ("vouchers", "voucher_no", 120, False),
    ("general_ledger_entries", "voucher_no", 120, False),
    ("source_transactions", "reference", 200, False),
    ("stock_movements", "reference", 200, True),
    ("inventory_valuation_layers", "source_id", 200, True),  # holds the purchase reference
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column, length, nullable in _COLUMNS:
        op.alter_column(table, column, type_=sa.String(length), existing_nullable=nullable)


def downgrade() -> None:
    pass  # narrowing could truncate saved numbers
