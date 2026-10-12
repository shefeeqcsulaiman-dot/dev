"""Purchases of a mapped outer pack count stock in the item's own units.

A stock mapping can say a supplier's line is an outer pack: units_per_outer = 24 means one
CTN on the purchase holds 24 of the item. Stock is kept in the item's own units (what sales
and POS take away), so a purchase line of 2 CTN at 120.00 adds 48 at 5.00 each. The line's
own quantity and unit are kept on the movement (source_quantity / source_unit) so the stock
history can still show "2 CTN = 48". Before 2026-10-12 the purchase quantity was stored as
is, so a purchase of 2 cartons added 2 while a sale of 10 pieces took away 10.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

_CENT = Decimal("0.01")
_COST = Decimal("0.0001")


def units_per_outer(mapping: Any) -> Decimal:
    """The mapping's pack size; 1 when unset, zero or negative."""
    try:
        factor = Decimal(str(getattr(mapping, "units_per_outer", None) or 1))
    except (ArithmeticError, ValueError):
        return Decimal(1)
    return factor if factor > 0 else Decimal(1)


def unit_cost(mapping: Any) -> Decimal:
    """Cost of one stock unit. A mapping's cost is what one purchase line unit (the outer
    pack) costs, so it's spread over the pack's units."""
    try:
        cost = Decimal(str(getattr(mapping, "cost", None) or 0))
    except (ArithmeticError, ValueError):
        return Decimal(0)
    return (cost / units_per_outer(mapping)).quantize(_COST)


def purchase_unit(line: dict[str, Any]) -> str | None:
    unit = str(line.get("unit") or line.get("unit_of_measure") or line.get("uom") or "").strip()
    return unit[:30] or None


def purchase_stock(mapping: Any, line: dict[str, Any], quantity: Decimal, unit_cost: Decimal) -> dict[str, Any]:
    """StockMovement fields for one purchase line: stock quantity and cost per stock unit,
    plus the line's own quantity and unit."""
    factor = units_per_outer(mapping)
    return {
        "quantity": (quantity * factor).quantize(_CENT),
        "unit_cost": (unit_cost / factor).quantize(_COST),
        "source_quantity": quantity,
        "source_unit": purchase_unit(line),
        "units_per_outer": factor,
    }
