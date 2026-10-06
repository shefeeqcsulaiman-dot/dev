"""Fill the app_data_records summary columns for expenses.

app/doc_index.py now stamps party/doc_status/doc_kind/amount/record_date on expenses
too (doc_kind: direct or other), for the paged expense list, its cards and the
dashboard direct-expense figure. No schema change.

Revision ID: 0004_expense_index
Revises: 0003_more_doc_indexes
Create Date: 2026-10-06
"""
import json

import sqlalchemy as sa
from alembic import op

from app.doc_index import doc_columns

revision = "0004_expense_index"
down_revision = "0003_more_doc_indexes"
branch_labels = None
depends_on = None

_BATCH = 500


def _load(payload):
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _records():
    return sa.table(
        "app_data_records",
        sa.column("id", sa.String), sa.column("collection", sa.String), sa.column("payload", sa.Text),
        sa.column("record_date", sa.String), sa.column("party", sa.String), sa.column("doc_status", sa.String),
        sa.column("doc_kind", sa.String), sa.column("salesperson", sa.String), sa.column("amount", sa.Numeric(14, 2)),
    )


def upgrade() -> None:
    bind = op.get_bind()
    records = _records()
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(records.c.id, records.c.payload)
            .where(records.c.collection == "expenses", records.c.id > last_id)
            .order_by(records.c.id).limit(_BATCH)
        ).all()
        if not rows:
            break
        for row_id, payload in rows:
            bind.execute(sa.update(records).where(records.c.id == row_id).values(**doc_columns(_load(payload), "expenses")))
        last_id = rows[-1][0]


def downgrade() -> None:
    records = _records()
    op.get_bind().execute(
        sa.update(records).where(records.c.collection == "expenses").values(
            party=None, doc_status=None, doc_kind=None, salesperson=None, amount=None,
        )
    )
