"""owner_key on app_data_records, filled for employee-owned workflow records.

The ESS portal listed an employee's tasks and requests (overtime, loans, salary advances,
attendance corrections) by loading and parsing every such record the company has, then
keeping the employee's own -- on every dashboard load, with no Redis cache in
production. app.models stamps owner_key (payload_owner_key()) on every save; this adds
the column and its index and fills it for what is already stored.

Revision ID: 0013_app_data_owner_key
Revises: 0012_stock_movement_ref_index
Create Date: 2026-10-09
"""
import sqlalchemy as sa
from alembic import op

from app.models import OWNER_COLLECTIONS, payload_owner_key

revision = "0013_app_data_owner_key"
down_revision = "0012_stock_movement_ref_index"
branch_labels = None
depends_on = None

_INDEX = "ix_app_data_company_collection_owner"
_BATCH = 1000


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "owner_key" not in {c["name"] for c in inspector.get_columns("app_data_records")}:
        op.add_column("app_data_records", sa.Column("owner_key", sa.String(160)))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("app_data_records")}:
        op.create_index(_INDEX, "app_data_records", ["company_id", "collection", "owner_key"])

    records = sa.table("app_data_records", sa.column("id"), sa.column("collection"),
                       sa.column("payload"), sa.column("owner_key"))
    last_id = ""
    while True:  # keyset batches: memory stays flat however many records there are
        rows = bind.execute(
            sa.select(records.c.id, records.c.collection, records.c.payload)
            .where(records.c.collection.in_(OWNER_COLLECTIONS), records.c.id > last_id)
            .order_by(records.c.id)
            .limit(_BATCH)
        ).all()
        if not rows:
            break
        for record_id, collection, payload in rows:
            bind.execute(
                sa.update(records).where(records.c.id == record_id)
                .values(owner_key=payload_owner_key(collection, payload))
            )
        last_id = rows[-1][0]


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="app_data_records")
    with op.batch_alter_table("app_data_records") as batch:  # SQLite can't drop a column in place
        batch.drop_column("owner_key")
