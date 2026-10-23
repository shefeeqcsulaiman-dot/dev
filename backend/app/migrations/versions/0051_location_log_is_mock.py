"""employee_location_logs.is_mock: the phone reported a fake (mock) GPS position. Such a
check-in is refused and listed for HR at GET /hr/gps-alerts.

Revision ID: 0051_location_log_is_mock
Revises: 0050_ess_refresh_tokens
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0051_location_log_is_mock"
down_revision = "0050_ess_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("employee_location_logs")}
    if "is_mock" not in columns:
        op.add_column("employee_location_logs",
                      sa.Column("is_mock", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("employee_location_logs", "is_mock")
