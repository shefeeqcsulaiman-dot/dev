"""catalog_products becomes the main copy of products (step C, like 0024 for customers).

Adds catalog_products.extra (a product's fields without a column of their own, its inline
image aside), so a row holds the whole product; rewrites each product's JSON as its row
regenerates it (app/catalog_products.py canonical_product(): prices numbers, same keys,
image kept); and refills the table.

Revision ID: 0025_products_main_copy
Revises: 0024_parties_main_copy
Create Date: 2026-10-12
"""
import json

import sqlalchemy as sa
from alembic import op

revision = "0025_products_main_copy"
down_revision = "0024_parties_main_copy"
branch_labels = None
depends_on = None

_BATCH = 500  # products may carry inline images
_COLUMNS = ("id", "company_id", "branch_id", "record_key", "code", "code_key", "name", "name_key", "type",
            "category", "unit", "selling_price", "cost", "vat", "tracking", "supplier_name", "barcode", "status",
            "extra")
_MONEY = {"selling_price", "cost"}


def upgrade() -> None:
    from app.catalog_products import COLLECTIONS, canonical_product, mirror

    bind = op.get_bind()
    if "extra" not in {c["name"] for c in sa.inspect(bind).get_columns("catalog_products")}:
        op.add_column("catalog_products", sa.Column("extra", sa.Text(), nullable=True))

    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    update = sa.update(records).where(records.c.id == sa.bindparam("_id")).values(payload=sa.bindparam("doc"))
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection.in_(COLLECTIONS), records.c.id > last_id)
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        values = []
        for row_id, payload in rows:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(doc, dict):
                canonical = canonical_product(doc)
                if canonical != doc:
                    values.append({"_id": row_id, "doc": json.dumps(canonical, ensure_ascii=False, default=str)})
        if values:
            bind.execute(update, values)
        last_id = rows[-1][0]

    bind.execute(sa.text("DELETE FROM catalog_products"))
    table = sa.table("catalog_products", *[sa.column(c, sa.Numeric(14, 2)) if c in _MONEY else sa.column(c)
                                           for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_column("catalog_products", "extra")
