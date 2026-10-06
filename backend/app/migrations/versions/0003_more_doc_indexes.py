"""Fill the app_data_records summary columns for bills, purchases, quotations, payments.

0002 added the columns (party, doc_status, doc_kind, salesperson, amount, amount_paid)
for sales invoices. app/doc_index.py now stamps them for these four collections too, so
this fills them in on existing rows, including amount_paid on bills and purchase records
from the supplier payments already saved. No schema change.

Revision ID: 0003_more_doc_indexes
Revises: 0002_sales_invoice_index
Create Date: 2026-10-06
"""
import json
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

from app.doc_index import doc_columns, side_allocations

revision = "0003_more_doc_indexes"
down_revision = "0002_sales_invoice_index"
branch_labels = None
depends_on = None

_COLLECTIONS = ("bills", "purchaseRecords", "quotations", "payments")
_PAYABLE = ("bills", "purchaseRecords")  # settled by supplier payments
_BATCH = 500


def _load(payload):
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    records = sa.table(
        "app_data_records",
        sa.column("id", sa.String), sa.column("company_id", sa.String), sa.column("collection", sa.String),
        sa.column("record_key", sa.String), sa.column("payload", sa.Text), sa.column("record_date", sa.String),
        sa.column("party", sa.String), sa.column("doc_status", sa.String), sa.column("doc_kind", sa.String),
        sa.column("salesperson", sa.String), sa.column("amount", sa.Numeric(14, 2)),
        sa.column("amount_paid", sa.Numeric(14, 2)),
    )

    payable_keys: dict[str, dict[str, list[str]]] = {}  # company -> lower(doc no) -> row ids
    for collection in _COLLECTIONS:
        last_id = ""
        while True:
            rows = bind.execute(
                sa.select(records.c.id, records.c.company_id, records.c.record_key, records.c.payload)
                .where(records.c.collection == collection, records.c.id > last_id)
                .order_by(records.c.id).limit(_BATCH)
            ).all()
            if not rows:
                break
            for row_id, company_id, key, payload in rows:
                values = doc_columns(_load(payload), collection)
                if collection in _PAYABLE:
                    values["amount_paid"] = Decimal("0")
                    ref = str(key or "").strip().lower()
                    if ref:
                        payable_keys.setdefault(company_id, {}).setdefault(ref, []).append(row_id)
                bind.execute(sa.update(records).where(records.c.id == row_id).values(**values))
            last_id = rows[-1][0]

    for company_id, keys in payable_keys.items():
        paid: dict[str, Decimal] = {}
        for (payload,) in bind.execute(
            sa.select(records.c.payload).where(records.c.company_id == company_id, records.c.collection == "payments")
        ):
            side, allocs = side_allocations(_load(payload))
            if side != "supplier":
                continue
            for ref, amount in allocs:
                if ref in keys:
                    paid[ref] = paid.get(ref, Decimal("0")) + amount
        for ref, amount in paid.items():
            bind.execute(sa.update(records).where(records.c.id.in_(keys[ref])).values(amount_paid=amount))


def downgrade() -> None:
    bind = op.get_bind()
    records = sa.table(
        "app_data_records", sa.column("collection", sa.String), sa.column("party", sa.String),
        sa.column("doc_status", sa.String), sa.column("doc_kind", sa.String), sa.column("salesperson", sa.String),
        sa.column("amount", sa.Numeric(14, 2)), sa.column("amount_paid", sa.Numeric(14, 2)),
    )
    bind.execute(
        sa.update(records).where(records.c.collection.in_(_COLLECTIONS)).values(
            party=None, doc_status=None, doc_kind=None, salesperson=None, amount=None, amount_paid=None,
        )
    )
