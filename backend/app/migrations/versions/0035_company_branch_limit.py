"""companies.max_branches: the branch limit Super Admin sets per company (NULL: no limit).

Revision ID: 0035_company_branch_limit
Revises: 0034_lookup_json_in_table
Create Date: 2026-10-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0035_company_branch_limit"
down_revision = "0034_lookup_json_in_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "max_branches" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("companies")}:
        op.add_column("companies", sa.Column("max_branches", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("companies", "max_branches")
