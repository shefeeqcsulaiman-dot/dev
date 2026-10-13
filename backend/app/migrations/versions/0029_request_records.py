"""request_records: the main copy of employee requests (step C).

Creates the table for overtime, loans, salary advances, attendance corrections and task
requests (typed columns plus extra) and fills it. A request's JSON is not rewritten: it
stays exactly as saved (app/request_records.py), only the columns hold parsed values.

Revision ID: 0029_request_records
Revises: 0028_document_records
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0029_request_records"
down_revision = "0028_document_records"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "employee_no", "employee_name", "department",
            "status", "is_open", "request_date", "amount", "hours", "months", "reason", "title", "extra")
_NUMBERS = {"amount", "hours", "months"}


def upgrade() -> None:
    from app.request_records import mirror

    bind = op.get_bind()
    if "request_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "request_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("collection", sa.String(80), nullable=False),
            sa.Column("record_key", sa.String(160)),
            sa.Column("employee_no", sa.String(80)),
            sa.Column("employee_name", sa.String(160)),
            sa.Column("department", sa.String(120)),
            sa.Column("status", sa.String(40)),
            sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("request_date", sa.String(10)),
            sa.Column("amount", sa.Numeric(18, 4)),
            sa.Column("hours", sa.Numeric(18, 4)),
            sa.Column("months", sa.Numeric(18, 4)),
            sa.Column("reason", sa.Text()),
            sa.Column("title", sa.String(255)),
            sa.Column("extra", sa.Text()),
        )
        op.create_index("ix_request_records_company_collection_employee", "request_records",
                        ["company_id", "collection", "employee_no"])
        op.create_index("ix_request_records_company_collection_open", "request_records",
                        ["company_id", "collection", "is_open"])
    bind.execute(sa.text("DELETE FROM request_records"))
    table = sa.table("request_records", *[sa.column(c, sa.Numeric(18, 4)) if c in _NUMBERS
                                          else sa.column(c, sa.Boolean()) if c == "is_open"
                                          else sa.column(c) for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_table("request_records")
