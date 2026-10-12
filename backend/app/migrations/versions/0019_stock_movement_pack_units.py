"""Purchases of a mapped outer pack count stock in the item's own units (app/stock_units.py).

stock_movements gains source_quantity / source_unit / units_per_outer (the purchase line's
own quantity and unit, and the pack size used) and unit_cost gets 4 decimals, so a pack
cost spread over its units doesn't round.

Existing purchase movements of items whose mapping has units_per_outer other than 1 were
stored in packs (2 CTN added 2 while sales take away pieces); they are re-counted the same
way new purchases are: quantity x pack size, cost / pack size, the original quantity kept
in source_quantity. Their purchase valuation layers are scaled with them. A movement that
already has source_quantity has been converted and is left alone, so this can't double up.

Revision ID: 0019_stock_movement_pack_units
Revises: 0018_task_records
Create Date: 2026-10-12
"""
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision = "0019_stock_movement_pack_units"
down_revision = "0018_task_records"
branch_labels = None
depends_on = None

_CENT = Decimal("0.01")
_COST = Decimal("0.0001")
# Typed so Decimal values bind on every driver (SQLite has no native decimal).
_QTY = sa.Numeric(12, 2)
_UNIT_COST = sa.Numeric(12, 4)


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("stock_movements")}
    with op.batch_alter_table("stock_movements") as batch:  # batch: SQLite rebuilds the table
        if "source_quantity" not in columns:
            batch.add_column(sa.Column("source_quantity", sa.Numeric(12, 2)))
        if "source_unit" not in columns:
            batch.add_column(sa.Column("source_unit", sa.String(30)))
        if "units_per_outer" not in columns:
            batch.add_column(sa.Column("units_per_outer", sa.Numeric(12, 4)))
        batch.alter_column("unit_cost", type_=sa.Numeric(12, 4), existing_type=sa.Numeric(12, 2))

    movements = sa.table(
        "stock_movements", sa.column("id"), sa.column("company_id"), sa.column("mapping_id"),
        sa.column("movement_type"), sa.column("reference"), sa.column("quantity", _QTY),
        sa.column("unit_cost", _UNIT_COST), sa.column("source_quantity", _QTY), sa.column("units_per_outer", _UNIT_COST),
    )
    mappings = sa.table("stock_product_mappings", sa.column("id"), sa.column("sku"), sa.column("units_per_outer"))
    layers = sa.table(
        "inventory_valuation_layers", sa.column("id"), sa.column("company_id"), sa.column("item_code"),
        sa.column("source_module"), sa.column("source_id"), sa.column("quantity_in", _QTY),
        sa.column("quantity_remaining", _QTY), sa.column("unit_cost", _UNIT_COST),
    )
    rows = bind.execute(
        sa.select(movements.c.id, movements.c.company_id, movements.c.reference, movements.c.quantity,
                  movements.c.unit_cost, mappings.c.sku, mappings.c.units_per_outer)
        .join(mappings, mappings.c.id == movements.c.mapping_id)
        .where(movements.c.movement_type == "purchase", movements.c.source_quantity.is_(None),
               mappings.c.units_per_outer.isnot(None), mappings.c.units_per_outer > 0,
               mappings.c.units_per_outer != 1)
    ).all()
    scaled_layers: set[tuple[str, str, str]] = set()
    for movement_id, company_id, reference, quantity, unit_cost, sku, factor in rows:
        factor = Decimal(str(factor))
        quantity = Decimal(str(quantity or 0))
        bind.execute(
            movements.update().where(movements.c.id == movement_id).values(
                source_quantity=quantity,
                units_per_outer=factor,
                quantity=(quantity * factor).quantize(_CENT),
                unit_cost=(Decimal(str(unit_cost or 0)) / factor).quantize(_COST),
            )
        )
        key = (company_id, reference or "", sku)
        if key in scaled_layers:
            continue  # one purchase's layers for this item are scaled once, together
        scaled_layers.add(key)
        for layer_id, q_in, q_left, cost in bind.execute(
            sa.select(layers.c.id, layers.c.quantity_in, layers.c.quantity_remaining, layers.c.unit_cost)
            .where(layers.c.company_id == company_id, layers.c.source_module == "purchase",
                   layers.c.source_id == reference, layers.c.item_code == sku)
        ).all():
            bind.execute(
                layers.update().where(layers.c.id == layer_id).values(
                    quantity_in=(Decimal(str(q_in or 0)) * factor).quantize(_CENT),
                    quantity_remaining=(Decimal(str(q_left or 0)) * factor).quantize(_CENT),
                    unit_cost=(Decimal(str(cost or 0)) / factor).quantize(_COST),
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("stock_movements") as batch:
        batch.drop_column("units_per_outer")
        batch.drop_column("source_unit")
        batch.drop_column("source_quantity")
