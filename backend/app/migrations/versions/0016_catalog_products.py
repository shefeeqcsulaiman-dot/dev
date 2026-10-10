"""catalog_products: products as real columns (step A of moving them out of JSON).

app/catalog_products.py keeps one row per "products" app-data record on every write;
this creates the table and fills it from the products already stored.

Revision ID: 0016_catalog_products
Revises: 0015_parties_table
Create Date: 2026-10-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0016_catalog_products"
down_revision = "0015_parties_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "code", "code_key", "name", "name_key", "type",
            "category", "unit", "selling_price", "cost", "vat", "tracking", "supplier_name", "barcode", "status")
_MONEY = {"selling_price", "cost"}


def upgrade() -> None:
    bind = op.get_bind()
    if "catalog_products" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "catalog_products",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("record_key", sa.String(160)),
            sa.Column("code", sa.String(120)),
            sa.Column("code_key", sa.String(120)),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("name_key", sa.String(255), nullable=False, server_default=""),
            sa.Column("type", sa.String(60)),
            sa.Column("category", sa.String(120)),
            sa.Column("unit", sa.String(40)),
            sa.Column("selling_price", sa.Numeric(14, 2)),
            sa.Column("cost", sa.Numeric(14, 2)),
            sa.Column("vat", sa.String(40)),
            sa.Column("tracking", sa.String(20)),
            sa.Column("supplier_name", sa.String(255)),
            sa.Column("barcode", sa.String(80)),
            sa.Column("status", sa.String(40)),
        )
        op.create_index("ix_catalog_products_company_name", "catalog_products", ["company_id", "name_key"])
        op.create_index("ix_catalog_products_company_code", "catalog_products", ["company_id", "code_key"])
    bind.execute(sa.text("DELETE FROM catalog_products"))
    from app.catalog_products import mirror

    table = sa.table("catalog_products", *[sa.column(c, sa.Numeric(14, 2)) if c in _MONEY else sa.column(c)
                                           for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_table("catalog_products")
