"""Pre-calculated account totals (account_period_totals), filled for every company.

Reports read posted debit/credit per account from here instead of adding up every
journal line on each request; app/account_totals.py keeps it current.

Revision ID: 0006_account_period_totals
Revises: 0005_posting_indexes
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

revision = "0006_account_period_totals"
down_revision = "0005_posting_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "account_period_totals" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "account_period_totals",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("account_id", sa.String(36), nullable=False),
            sa.Column("period", sa.String(7), nullable=False),
            sa.Column("debit", sa.Numeric(16, 2)),
            sa.Column("credit", sa.Numeric(16, 2)),
        )
        op.create_index("ix_account_period_totals_company_period", "account_period_totals", ["company_id", "period"])
        op.create_index("ix_account_period_totals_company_account", "account_period_totals", ["company_id", "account_id"])

    from app.account_totals import rebuild_company

    session = Session(bind=bind)
    companies = [c for (c,) in bind.execute(sa.text("SELECT DISTINCT company_id FROM journal_entries"))]
    for company_id in companies:
        rebuild_company(session, company_id)
    session.flush()


def downgrade() -> None:
    op.drop_table("account_period_totals")
