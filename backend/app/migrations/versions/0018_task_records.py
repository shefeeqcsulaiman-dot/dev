"""task_records: tasks as real columns (step A of moving them out of JSON).

app/task_records.py keeps one row per "tasks" app-data record on every write; this
creates the table and fills it from the tasks already stored. The bootstrap then finds
old open tasks through the indexed is_open column instead of decoding every task.

Revision ID: 0018_task_records
Revises: 0017_app_data_row_version
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0018_task_records"
down_revision = "0017_app_data_row_version"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "title", "department", "assigned_to", "assigned_to_name",
            "status", "is_open", "priority", "progress", "due_date", "repeat")


def upgrade() -> None:
    bind = op.get_bind()
    if "task_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "task_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("record_key", sa.String(160)),
            sa.Column("title", sa.String(255), nullable=False, server_default=""),
            sa.Column("department", sa.String(120)),
            sa.Column("assigned_to", sa.String(80)),
            sa.Column("assigned_to_name", sa.String(160)),
            sa.Column("status", sa.String(40)),
            sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("priority", sa.String(20)),
            sa.Column("progress", sa.Integer()),
            sa.Column("due_date", sa.String(10)),
            sa.Column("repeat", sa.String(20)),
        )
        op.create_index("ix_task_records_company_open", "task_records", ["company_id", "is_open"])
        op.create_index("ix_task_records_company_assignee", "task_records", ["company_id", "assigned_to", "status"])
        op.create_index("ix_task_records_company_due", "task_records", ["company_id", "due_date"])
    # Filled by 0026_tasks_main_copy, which adds task_records.extra: app/task_records.py's rows
    # now carry it, so filling here (before that column exists) would fail on a new database.


def downgrade() -> None:
    op.drop_table("task_records")
