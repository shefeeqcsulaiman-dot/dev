"""row_version / updated_by / updated_by_name on app_data_records: edit-conflict check.

Saving an app-data record overwrote the whole JSON payload with whatever the browser
sent, so when two people edited the same record (an invoice, a rota day, a task) the
later save silently wiped the earlier one. app.models now bumps row_version and records
who changed the record on every payload change; save_app_record() refuses a save based
on an older version when someone else saved in between. Existing rows start at version 1
with no known editor. Adding columns with a constant default is a metadata-only change
on PostgreSQL 11+, so this is instant however many rows there are.

Revision ID: 0017_app_data_row_version
Revises: 0016_catalog_products
Create Date: 2026-10-11
"""
import sqlalchemy as sa
from alembic import op

revision = "0017_app_data_row_version"
down_revision = "0016_catalog_products"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("app_data_records")}
    if "row_version" not in columns:
        op.add_column("app_data_records", sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"))
    if "updated_by" not in columns:
        op.add_column("app_data_records", sa.Column("updated_by", sa.String(80)))
    if "updated_by_name" not in columns:
        op.add_column("app_data_records", sa.Column("updated_by_name", sa.String(160)))


def downgrade() -> None:
    with op.batch_alter_table("app_data_records") as batch:  # SQLite can't drop a column in place
        batch.drop_column("updated_by_name")
        batch.drop_column("updated_by")
        batch.drop_column("row_version")
