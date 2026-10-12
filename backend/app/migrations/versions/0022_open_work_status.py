"""Workflow records' status in app_data_records.doc_status, filled for existing records.

The bootstrap tops each open-work list (overtime, leave, corrections, loans, advances,
requisitions, candidates, rota swaps/approvals, task requests) up with its still-open older
items, and found them by decoding every older record's JSON on each load. models.py now
stamps the status (app/workflow_status.py status_key()) on every save; this fills it for
what is already stored, so finished items are skipped in SQL.

Revision ID: 0022_open_work_status
Revises: 0021_document_due_date
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

from app.workflow_status import OPEN_WORK_COLLECTIONS, status_key

revision = "0022_open_work_status"
down_revision = "0021_document_due_date"
branch_labels = None
depends_on = None

_BATCH = 1000


def upgrade() -> None:
    bind = op.get_bind()
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text), sa.column("doc_status", sa.String))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(doc_status=sa.bindparam("status"))
    for collection in sorted(OPEN_WORK_COLLECTIONS):
        last_id = ""
        while True:
            rows = bind.execute(
                sa.select(records.c.id, records.c.payload)
                .where(records.c.collection == collection, records.c.id > last_id)
                .order_by(records.c.id).limit(_BATCH)
            ).all()
            if not rows:
                break
            values = [{"_id": row_id, "status": status_key(payload)} for row_id, payload in rows]
            values = [v for v in values if v["status"]]
            if values:
                bind.execute(update, values)
            last_id = rows[-1][0]


def downgrade() -> None:
    records = sa.table("app_data_records", sa.column("collection", sa.String), sa.column("doc_status", sa.String))
    op.get_bind().execute(sa.update(records).where(records.c.collection.in_(sorted(OPEN_WORK_COLLECTIONS)))
                          .values(doc_status=None))
