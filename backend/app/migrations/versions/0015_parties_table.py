"""parties: customers and vendors as real columns (step A of moving them out of JSON).

app/parties.py keeps one row per "customers"/"vendors" app-data record on every write;
this creates the table and fills it from the records already stored.

Revision ID: 0015_parties_table
Revises: 0014_widen_entry_numbers
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0015_parties_table"
down_revision = "0014_widen_entry_numbers"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "kind", "record_key", "name", "name_key", "trn", "email", "phone",
            "address", "emirate", "category", "contact", "credit_limit", "status")


def upgrade() -> None:
    bind = op.get_bind()
    if "parties" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "parties",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("kind", sa.String(20), nullable=False),
            sa.Column("record_key", sa.String(160)),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("name_key", sa.String(255), nullable=False, server_default=""),
            sa.Column("trn", sa.String(40)),
            sa.Column("email", sa.String(255)),
            sa.Column("phone", sa.String(80)),
            sa.Column("address", sa.Text),
            sa.Column("emirate", sa.String(80)),
            sa.Column("category", sa.String(120)),
            sa.Column("contact", sa.String(255)),
            sa.Column("credit_limit", sa.Numeric(14, 2)),
            sa.Column("status", sa.String(40)),
        )
        op.create_index("ix_parties_company_kind_name", "parties", ["company_id", "kind", "name_key"])
        op.create_index("ix_parties_company_kind_trn", "parties", ["company_id", "kind", "trn"])
    # Filled by 0024_parties_main_copy, which adds parties.extra: app/parties.py's rows now
    # carry it, so filling here (before that column exists) would fail on a new database.


def downgrade() -> None:
    op.drop_table("parties")
