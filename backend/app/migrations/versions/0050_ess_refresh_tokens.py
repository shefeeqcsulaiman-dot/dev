"""ess_refresh_tokens: long-lived mobile-app sign-ins for ESS (POST /ess/refresh), so
automatic GPS check-in keeps working after the 24h access token expires.

Revision ID: 0050_ess_refresh_tokens
Revises: 0049_support_tickets
Create Date: 2026-10-22
"""
import sqlalchemy as sa
from alembic import op

revision = "0050_ess_refresh_tokens"
down_revision = "0049_support_tickets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "ess_refresh_tokens" in sa.inspect(op.get_bind()).get_table_names():
        return  # created by Base.metadata.create_all() on a fresh database
    op.create_table(
        "ess_refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.String(36), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("device", sa.String(120)),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ess_refresh_tokens_company_id", "ess_refresh_tokens", ["company_id"])
    op.create_index("ix_ess_refresh_tokens_employee_id", "ess_refresh_tokens", ["employee_id"])


def downgrade() -> None:
    op.drop_table("ess_refresh_tokens")
