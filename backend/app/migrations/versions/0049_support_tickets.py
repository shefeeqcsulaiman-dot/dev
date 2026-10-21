"""support_tickets / support_messages: company users raise support tickets to the platform
team (Super Admin > Support Tickets) and both sides reply in the ticket.

Revision ID: 0049_support_tickets
Revises: 0048_company_recycle_bin
Create Date: 2026-10-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0049_support_tickets"
down_revision = "0048_company_recycle_bin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "support_tickets" not in tables:
        op.create_table(
            "support_tickets",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("number", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("subject", sa.String(200), nullable=False),
            sa.Column("category", sa.String(40)),
            sa.Column("priority", sa.String(20)),
            sa.Column("status", sa.String(20)),
            sa.Column("raised_by_name", sa.String(160)),
            sa.Column("raised_by_email", sa.String(255)),
            sa.Column("last_message_at", sa.DateTime(timezone=True)),
            sa.Column("unread_company", sa.Boolean()),
            sa.Column("unread_support", sa.Boolean()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_support_tickets_company_updated", "support_tickets", ["company_id", "updated_at"])
        op.create_index("ix_support_tickets_status_updated", "support_tickets", ["status", "updated_at"])
    if "support_messages" not in tables:
        op.create_table(
            "support_messages",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("ticket_id", sa.String(36), sa.ForeignKey("support_tickets.id"), nullable=False),
            sa.Column("side", sa.String(10), nullable=False),
            sa.Column("author_name", sa.String(160)),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("attachment", sa.Text()),
            sa.Column("attachment_name", sa.String(200)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_support_messages_ticket", "support_messages", ["ticket_id", "created_at"])


def downgrade() -> None:
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
