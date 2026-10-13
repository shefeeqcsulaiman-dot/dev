"""lookup_records: the main copy of the small lookup lists (step C).

Creates the table for units, sales categories, service types, salespeople, shift setup,
holidays and bank accounts (code, name, status, extra) and fills it; their JSON is left as
saved (app/lookup_records.py).

Revision ID: 0033_lookup_records
Revises: 0032_more_request_records
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0033_lookup_records"
down_revision = "0032_more_request_records"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "code", "name", "status", "extra")


def upgrade() -> None:
    from app.lookup_records import mirror

    bind = op.get_bind()
    if "lookup_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "lookup_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("collection", sa.String(80), nullable=False),
            sa.Column("record_key", sa.String(160)),
            sa.Column("code", sa.String(120)),
            sa.Column("name", sa.String(255)),
            sa.Column("status", sa.String(40)),
            sa.Column("extra", sa.Text()),
        )
        op.create_index("ix_lookup_records_company_collection_name", "lookup_records", ["company_id", "collection", "name"])
    bind.execute(sa.text("DELETE FROM lookup_records"))
    mirror.refill_all(bind, sa.table("lookup_records", *[sa.column(c) for c in _COLUMNS]))


def downgrade() -> None:
    op.drop_table("lookup_records")
