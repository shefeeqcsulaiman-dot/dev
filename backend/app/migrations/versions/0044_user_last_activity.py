"""users.last_activity: when a company user last made a request (at most once a minute),
like employees.last_activity and branches.last_activity. Feeds Super Admin's
"users online / active" counts.

Revision ID: 0044_user_last_activity
Revises: 0043_purchase_document_table
Create Date: 2026-10-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_user_last_activity"
down_revision = "0043_purchase_document_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "last_activity" not in columns:
        op.add_column("users", sa.Column("last_activity", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_activity")
