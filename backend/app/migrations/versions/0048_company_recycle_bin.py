"""companies.deleted_at / deleted_by / deleted_backup_key / deleted_prev_expiry: Super Admin's
Delete Company moves a company to a 7-day recycle bin (after a backup) instead of removing it.

Revision ID: 0048_company_recycle_bin
Revises: 0047_leave_attachment
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0048_company_recycle_bin"
down_revision = "0047_leave_attachment"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("deleted_at", sa.DateTime(timezone=True)),
    ("deleted_by", sa.String(160)),
    ("deleted_backup_key", sa.String(300)),
    ("deleted_prev_expiry", sa.String(20)),
)


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("companies")}
    for name, type_ in _COLUMNS:
        if name not in columns:
            op.add_column("companies", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("companies", name)
