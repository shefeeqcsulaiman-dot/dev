"""request_records also holds payroll adjustments, rota swaps / approvals, job requisitions
and candidates. Their JSON is left as saved (app/request_records.py); this refills the table.

Revision ID: 0032_more_request_records
Revises: 0031_more_document_records
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0032_more_request_records"
down_revision = "0031_more_document_records"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "employee_no", "employee_name", "department",
            "status", "is_open", "request_date", "amount", "hours", "months", "reason", "title", "extra")
_NUMBERS = {"amount", "hours", "months"}


def upgrade() -> None:
    from app.request_records import mirror

    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM request_records"))
    table = sa.table("request_records", *[sa.column(c, sa.Numeric(18, 4)) if c in _NUMBERS
                                          else sa.column(c, sa.Boolean()) if c == "is_open"
                                          else sa.column(c) for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    t = sa.table("request_records", sa.column("collection", sa.String))
    op.get_bind().execute(sa.delete(t).where(t.c.collection.in_(
        ("payrollAdjustments", "rotaSwaps", "rotaApprovals", "jobRequisitions", "candidates"))))
