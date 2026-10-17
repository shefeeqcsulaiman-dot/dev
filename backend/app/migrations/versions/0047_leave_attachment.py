"""leave_requests.attachment / attachment_name: a sick note or photo attached to a leave
request from ESS (a data URL, image or PDF, at most ~3 MB).

Revision ID: 0047_leave_attachment
Revises: 0045_document_dates
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op

revision = "0047_leave_attachment"
down_revision = "0045_document_dates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("leave_requests")}
    if "attachment" not in columns:
        op.add_column("leave_requests", sa.Column("attachment", sa.Text(), nullable=True))
    if "attachment_name" not in columns:
        op.add_column("leave_requests", sa.Column("attachment_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("leave_requests", "attachment_name")
    op.drop_column("leave_requests", "attachment")
