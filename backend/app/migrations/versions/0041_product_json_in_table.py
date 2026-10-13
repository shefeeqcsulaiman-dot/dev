"""Step D for products: their JSON is no longer stored (app/record_payloads.py).

Adds catalog_products.image (a product's inline image, which until now lived only in the
JSON), refills the table, then empties each product's stored JSON -- only where its row
rebuilds exactly the same JSON; downgrade stores it again and drops the column.

Revision ID: 0041_product_json_in_table
Revises: 0040_employee_json_in_table
Create Date: 2026-10-13
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision = "0041_product_json_in_table"
down_revision = "0040_employee_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "code", "code_key", "name", "name_key", "type",
            "category", "unit", "selling_price", "cost", "vat", "tracking", "supplier_name", "barcode", "status",
            "extra", "image")
_MONEY = {"selling_price", "cost"}
_BATCH = 200  # products may carry inline images


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("catalog_products", *[sa.column(c, sa.Numeric(14, 2)) if c in _MONEY else sa.column(c)
                                          for c in _COLUMNS])
    return records, rows


def _rebuild(row) -> dict:
    from app.catalog_products import rebuilt_product

    return rebuilt_product(SimpleNamespace(**dict(row)))


def upgrade() -> None:
    from app.catalog_products import mirror

    bind = op.get_bind()
    if "image" not in {c["name"] for c in sa.inspect(bind).get_columns("catalog_products")}:
        op.add_column("catalog_products", sa.Column("image", sa.Text(), nullable=True))
    records, rows_table = _tables()
    bind.execute(sa.text("DELETE FROM catalog_products"))
    mirror.refill_all(bind, rows_table, batch=_BATCH)
    last_id = ""
    while True:
        rows = bind.execute(sa.select(records.c.id, records.c.payload)
                            .where(records.c.collection == "products", records.c.id > last_id, records.c.payload != "")
                            .order_by(records.c.id).limit(_BATCH)).all()
        if not rows:
            break
        typed = {r["id"]: r for r in bind.execute(sa.select(rows_table).where(rows_table.c.id.in_([i for i, _ in rows])))
                 .mappings()}
        blank = []
        for row_id, payload in rows:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            row = typed.get(row_id)
            if row is not None and isinstance(doc, dict) and _rebuild(row) == doc:
                blank.append(row_id)
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = rows[-1][0]


def downgrade() -> None:
    bind = op.get_bind()
    records, rows_table = _tables()
    for row in bind.execute(sa.select(rows_table).join(records, records.c.id == rows_table.c.id)
                            .where(records.c.payload == "")).mappings().all():
        bind.execute(sa.update(records).where(records.c.id == row["id"])
                     .values(payload=json.dumps(_rebuild(row), ensure_ascii=False, default=str)))
    op.drop_column("catalog_products", "image")
