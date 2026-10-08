"""Indexes for the 5-minute stale check-out sweep and the Super Admin lists.

- attendance_sessions (status, check_in): the sweep (hr.auto_checkout_stale_sessions)
  looks for open sessions checked in before a cutoff. status had no index of its own, so
  every run scanned every session ever recorded, across all companies.
- employee_location_logs (session_id, created_at): latest ping per open session.
- users (company_id, last_login): the Super Admin company list sorts and filters by each
  company's last sign-in.

Revision ID: 0008_scheduled_job_indexes
Revises: 0007_foreign_key_indexes
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_scheduled_job_indexes"
down_revision = "0007_foreign_key_indexes"
branch_labels = None
depends_on = None

_INDEXES = (
    ("ix_att_session_status_check_in", "attendance_sessions", ["status", "check_in"]),
    ("ix_emp_loc_log_session_created", "employee_location_logs", ["session_id", "created_at"]),
    ("ix_users_company_last_login", "users", ["company_id", "last_login"]),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for name, table, cols in _INDEXES:
        if name not in {ix["name"] for ix in inspector.get_indexes(table)}:
            op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table, _cols in _INDEXES:
        op.drop_index(name, table_name=table)
