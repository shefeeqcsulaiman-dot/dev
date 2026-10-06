"""Summary columns on app_data_records for paged, server-side sales invoice lists.

Adds party / doc_status / doc_kind / salesperson / amount / amount_paid (see
app/doc_index.py), two indexes, and fills them in for every existing salesInvoices
row, including amount_paid from the customer payments already saved.

Revision ID: 0002_sales_invoice_index
Revises: 0001_baseline
Create Date: 2026-10-06
"""
import json
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.doc_index import doc_columns, payment_allocations

revision = "0002_sales_invoice_index"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("party", sa.String(160)),
    ("doc_status", sa.String(40)),
    ("doc_kind", sa.String(20)),
    ("salesperson", sa.String(120)),
    ("amount", sa.Numeric(14, 2)),
    ("amount_paid", sa.Numeric(14, 2)),
)
_INDEXES = (
    ("ix_app_data_company_collection_status", ["company_id", "collection", "doc_status"]),
    ("ix_app_data_company_collection_party", ["company_id", "collection", "party"]),
)
_BATCH = 500


def _load(payload):
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("app_data_records")}
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column("app_data_records", sa.Column(name, type_, nullable=True))
    existing_ix = {ix["name"] for ix in inspector.get_indexes("app_data_records")}
    for name, cols in _INDEXES:
        if name not in existing_ix:
            op.create_index(name, "app_data_records", cols)

    records = sa.table(
        "app_data_records",
        sa.column("id", sa.String), sa.column("company_id", sa.String), sa.column("collection", sa.String),
        sa.column("record_key", sa.String), sa.column("payload", sa.Text), sa.column("record_date", sa.String),
        *(sa.column(name, type_) for name, type_ in _COLUMNS),
    )

    # 1. Summary columns for every sales invoice, in id order so batches never overlap.
    invoice_keys: dict[str, dict[str, list[str]]] = {}  # company -> lower(invoice no) -> row ids
    last_id = ""
    while True:
        rows = bind.execute(
            sa.select(records.c.id, records.c.company_id, records.c.record_key, records.c.payload)
            .where(records.c.collection == "salesInvoices", records.c.id > last_id)
            .order_by(records.c.id).limit(_BATCH)
        ).all()
        if not rows:
            break
        for row_id, company_id, key, payload in rows:
            values = doc_columns(_load(payload))
            values["amount_paid"] = Decimal("0")
            bind.execute(sa.update(records).where(records.c.id == row_id).values(**values))
            ref = str(key or "").strip().lower()
            if ref:
                invoice_keys.setdefault(company_id, {}).setdefault(ref, []).append(row_id)
        last_id = rows[-1][0]

    # 2. amount_paid from each company's customer payments.
    for company_id, keys in invoice_keys.items():
        paid: dict[str, Decimal] = {}
        for (payload,) in bind.execute(
            sa.select(records.c.payload).where(records.c.company_id == company_id, records.c.collection == "payments")
        ):
            for ref, amount in payment_allocations(_load(payload)):
                if ref in keys:
                    paid[ref] = paid.get(ref, Decimal("0")) + amount
        for ref, amount in paid.items():
            bind.execute(sa.update(records).where(records.c.id.in_(keys[ref])).values(amount_paid=amount))


def downgrade() -> None:
    for name, _cols in _INDEXES:
        op.drop_index(name, table_name="app_data_records")
    with op.batch_alter_table("app_data_records") as batch:
        for name, _type in reversed(_COLUMNS):
            batch.drop_column(name)
