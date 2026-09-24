import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth_principal import resolve_active_branch
from app.database import get_db
from app.dependencies import Principal, get_current_principal, get_current_user, require_module
from app.routers.reports import _cached_or_build
from app.models import AppDataRecord, InventoryValuationLayer, ItemUnit, ItemUnitConversion, StockAdjustmentApproval, StockMovement, StockProductMapping, User, Warehouse
from app.schemas import (
    InventoryValuationLayerOut,
    ItemUnitConversionIn,
    ItemUnitConversionOut,
    ItemUnitIn,
    ItemUnitOut,
    StockAdjustmentApprovalIn,
    StockAdjustmentApprovalOut,
    StockMappingIn,
    StockMappingOut,
    WarehouseIn,
    WarehouseOut,
)


router = APIRouter(tags=["inventory"], dependencies=[Depends(require_module("inventory"))])

# ── Demo product detection ───────────────────────────────────────────────────
_DEMO_REFERENCE_RE = re.compile(r"^(INV|PUR|QTN|BILL|PO|RCT)-2024-", re.IGNORECASE)
_DEMO_SKUS: set[str] = {
    "PRD-001", "STL-12MM", "PKG-BOX-A", "OIL-5L", "GLV-SAFE", "LOG-LOCAL",
    "OFF-CHAIR", "PPE-HELMET", "ELE-CABLE", "PKG-BOX", "PRN-FLYER",
    "FUEL-DIESEL", "IT-MON24", "UNI-STAFF", "WTR-CASE", "MNT-HOUR",
    "COU-DOC", "TLS-DRILL", "WH-SPACE", "PPE-VEST", "JAN-CLEAN",
    "IT-LAP15", "ELE-LED", "TLS-HAM",
}
_DEMO_NAMES: set[str] = {
    "steel rods 12mm", "packaging box a", "industrial oil 5l", "safety gloves",
    "corrugated box a", "safety helmet", "copper cable roll", "ergonomic office chair",
    "business laptop 15 inch", "diesel supply", "document courier",
    "maintenance technician hour", "high visibility vest", "local delivery service",
    "corrugated packing box", "printed flyer pack", "24 inch led monitor",
    "staff uniform set", "drinking water case", "cordless drill machine",
    "warehouse space rental", "deep cleaning service", "led panel light",
    "industrial hammer",
}
_DEMO_SUPPLIERS: set[str] = {
    "al hamad steel", "gulf freight", "office depot uae", "uae paints co",
    "uae paints co.", "gulf logistics ltd", "emirates supplies", "al baraka trading",
}


def _is_demo_sku_or_name(sku: str, name: str) -> bool:
    return sku.upper() in _DEMO_SKUS or name.lower() in _DEMO_NAMES


def _is_demo_purchase_record(record: dict) -> bool:
    # Any single signal (ref pattern, supplier name, product names) is common
    # enough in real UAE business data on its own to false-positive (e.g. a
    # genuine 2024-dated PO, or a real product literally named "Safety
    # Gloves"). Requiring at least 2 of 3 to agree matches how the actual
    # seeded demo dataset looks (consistently demo across every field) while
    # sparing real records that only coincidentally share one trait.
    signals = 0
    ref = str(record.get("ref") or record.get("invoice_no") or record.get("reference") or "").strip()
    if _DEMO_REFERENCE_RE.match(ref):
        signals += 1
    supplier = str(record.get("supplier") or "").strip().lower()
    if supplier in _DEMO_SUPPLIERS:
        signals += 1
    lines = record.get("lines") or []
    if lines and all(
        _is_demo_sku_or_name(
            str(ln.get("sku") or ln.get("code") or ""),
            str(ln.get("product") or ln.get("name") or ln.get("description") or ""),
        )
        for ln in lines
        if isinstance(ln, dict)
    ):
        signals += 1
    return signals >= 2


def consume_valuation_layers(db: Session, company_id: str, item_code: str, quantity: Decimal) -> None:
    """FIFO-consume `quantity` units from this item's valuation layers,
    oldest layer first, decrementing quantity_remaining. Layers were
    previously written once at purchase time (sync_purchase_stock(),
    backfill_purchase_stock_movements()) and never touched again — the
    ledger only ever grew, permanently mislabeled "FIFO" since nothing
    ever consumed it. Currently called for POS sales and negative stock
    adjustments; a returned sale is not yet re-added as a new layer (which
    lot it returns to is genuinely ambiguous without deeper cost tracking)
    — same deliberate scope line sync_pos_stock() already draws around
    valuation layers, just moved one step forward. Insufficient layers
    (selling more than any purchase ever added) isn't an error: this only
    keeps the FIFO costing ledger consistent with what stock existed to
    consume — SUM(StockMovement.quantity) remains the authoritative total
    regardless of what these layers show."""
    remaining = quantity
    if remaining <= 0:
        return
    layers = (
        db.query(InventoryValuationLayer)
        .filter(
            InventoryValuationLayer.company_id == company_id,
            InventoryValuationLayer.item_code == item_code,
            InventoryValuationLayer.quantity_remaining > 0,
        )
        .order_by(InventoryValuationLayer.created_at.asc())
        .all()
    )
    for layer in layers:
        if remaining <= 0:
            break
        take = min(layer.quantity_remaining, remaining)
        layer.quantity_remaining -= take
        remaining -= take


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[Warehouse]:
    return db.query(Warehouse).filter(Warehouse.company_id == current_user.company_id).order_by(Warehouse.name).all()


@router.post("/warehouses", response_model=WarehouseOut, status_code=201)
def create_warehouse(
    payload: WarehouseIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Warehouse:
    warehouse = Warehouse(company_id=current_user.company_id, **payload.model_dump())
    db.add(warehouse)
    db.commit()
    db.refresh(warehouse)
    return warehouse


@router.get("/inventory/mappings", response_model=list[StockMappingOut])
def list_mappings(db: Session = Depends(get_db), principal: Principal = Depends(get_current_principal)) -> list[StockProductMapping]:
    # Widened to Employee/branch principals in Branch Management Phase 5 —
    # StockProductMapping itself is shared company-wide master data (the
    # product catalog), so unlike stock-levels/stock-movements below, no
    # branch filter is applied here; every principal in the company sees
    # the same mapping list.
    if not inventory_backfill_disabled(db, principal.company_id):
        backfill_purchase_stock_movements(db, principal)
    mappings = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == principal.company_id)
        .order_by(StockProductMapping.created_at.desc(), StockProductMapping.id.desc())
        .all()
    )
    hydrate_mapping_costs_from_purchase_data(db, principal.company_id, mappings)
    return mappings


@router.get("/inventory/stock-levels")
def list_stock_levels(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    if not inventory_backfill_disabled(db, principal.company_id):
        # Always runs uncached — it's the one part of this endpoint that
        # writes (new StockMovement/valuation rows for orphaned legacy
        # purchases), and is already cheap when there's nothing new to
        # backfill (see its own docstring/comment). Only the read/aggregate
        # portion below is cached.
        backfill_purchase_stock_movements(db, principal)
    can_cross_branch = principal.can_cross_branch("inventory")
    resolved_branch_id = branch_id if can_cross_branch else resolve_active_branch(principal, branch_id)
    # Same "hit on every page load" shape as reports.dashboard — cache key
    # includes the cross-branch mode as well as the resolved branch: a
    # cross-branch viewer's exact-branch-match join and a branch-scoped
    # viewer's (branch-match OR NULL-branch) join are different queries
    # that can return different totals for the same company+branch_id, so
    # they must never share a cache entry.
    mode = "cross" if can_cross_branch else "scoped"
    cache_key = f"stock_levels:{principal.company_id}:{mode}:{resolved_branch_id or 'all'}"
    return _cached_or_build(cache_key, 60, lambda: _build_stock_levels(db, principal.company_id, resolved_branch_id, can_cross_branch))


def _build_stock_levels(db: Session, company_id: str, resolved_branch_id: str | None, can_cross_branch: bool) -> list[dict[str, object]]:
    movement_join_condition = (StockMovement.mapping_id == StockProductMapping.id) & (StockMovement.company_id == company_id)
    if can_cross_branch:
        if resolved_branch_id:
            movement_join_condition = movement_join_condition & (StockMovement.branch_id == resolved_branch_id)
    else:
        # A branch-scoped viewer sees stock quantities from their own
        # branch's movements only (plus branch-less legacy movements) —
        # each branch's physical stock is a separate count, not a shared
        # pool. Unassigned employees/the company admin still see the
        # full company-wide total, unchanged from before this phase.
        if resolved_branch_id:
            movement_join_condition = movement_join_condition & (
                (StockMovement.branch_id == resolved_branch_id) | (StockMovement.branch_id.is_(None))
            )
    rows = (
        db.query(
            StockProductMapping,
            func.coalesce(func.sum(StockMovement.quantity), 0).label("current_stock"),
        )
        .outerjoin(StockMovement, movement_join_condition)
        .filter(StockProductMapping.company_id == company_id)
        .group_by(StockProductMapping.id)
        .order_by(StockProductMapping.sku)
        .all()
    )
    # Consolidate duplicate mappings by normalised key (legacy data may have case variants)
    consolidated: dict[str, dict] = {}
    for mapping, current_stock in rows:
        key = (mapping.taxflow_name or mapping.name or mapping.sku or "").strip().lower()
        if key in consolidated:
            entry = consolidated[key]
            entry["current_stock"] = float(entry["current_stock"]) + float(current_stock)
            # Weighted average cost across both entries
            old_qty = float(entry.get("_qty_for_avg", 0))
            new_qty = float(current_stock)
            old_cost = float(entry["cost"] or 0)
            new_cost = float(mapping.cost or 0)
            total_qty = old_qty + new_qty
            if total_qty > 0:
                entry["cost"] = round((old_cost * old_qty + new_cost * new_qty) / total_qty, 6)
            entry["_qty_for_avg"] = total_qty
        else:
            consolidated[key] = {
                "code": mapping.sku,
                "name": mapping.taxflow_name or mapping.name,
                "category": "Purchases",
                "current_stock": float(current_stock),
                "unit": "PCS",
                "reorder_level": mapping.reorder_level,
                "cost": float(mapping.cost or 0),
                "_qty_for_avg": float(current_stock),
            }
    result = []
    for entry in consolidated.values():
        entry.pop("_qty_for_avg", None)
        # Non-blocking signal only — stock is allowed to go negative (a sale
        # can still complete when the recorded purchase history undercounts
        # what's physically on the shelf), but nothing anywhere previously
        # surfaced that it had happened, so it stayed invisible.
        entry["negative_stock"] = entry["current_stock"] < 0
        result.append(entry)
    return result


@router.get("/inventory/stock-movements")
def list_stock_movements(
    branch_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
) -> list[dict[str, object]]:
    if not inventory_backfill_disabled(db, principal.company_id):
        backfill_purchase_stock_movements(db, principal)
    query = (
        db.query(StockMovement, StockProductMapping)
        .join(StockProductMapping, StockMovement.mapping_id == StockProductMapping.id)
        .filter(StockMovement.company_id == principal.company_id)
    )
    if principal.can_cross_branch("inventory"):
        if branch_id:
            query = query.filter(StockMovement.branch_id == branch_id)
    else:
        active_branch = resolve_active_branch(principal, branch_id)
        if active_branch:
            query = query.filter((StockMovement.branch_id == active_branch) | (StockMovement.branch_id.is_(None)))
    rows = query.order_by(StockMovement.created_at.desc()).limit(500).all()
    # Build a lookup: reference → purchase record payload (for vendor/date)
    references = list({m.reference for m, _ in rows if m.reference})
    purchase_meta: dict[str, dict] = {}
    if references:
        pr_records = (
            db.query(AppDataRecord)
            .filter(
                AppDataRecord.company_id == principal.company_id,
                AppDataRecord.collection == "purchaseRecords",
                AppDataRecord.record_key.in_(references),
            )
            .all()
        )
        for pr in pr_records:
            try:
                payload = json.loads(pr.payload or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            purchase_meta[pr.record_key] = payload
    return [
        {
            "date": (
                purchase_meta.get(m.reference or "", {}).get("date")
                or (m.created_at.strftime("%Y-%m-%d") if m.created_at else "")
            ),
            "movement_type": m.movement_type,
            "item_name": mapping.taxflow_name or mapping.name or mapping.sku or "",
            "display_name": mapping.name or mapping.sku or "",
            "vendor_name": (
                purchase_meta.get(m.reference or "", {}).get("supplier")
                or purchase_meta.get(m.reference or "", {}).get("vendor")
                or purchase_meta.get(m.reference or "", {}).get("contact")
                or ""
            ),
            "quantity": float(m.quantity),
            "unit_cost": float(m.unit_cost),
            "reference": m.reference or "",
        }
        for m, mapping in rows
    ]


@router.delete("/inventory/stock-levels")
def clear_stock_levels(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> dict[str, object]:
    company_id = current_user.company_id
    movements = db.query(StockMovement).filter(StockMovement.company_id == company_id).delete(synchronize_session=False)
    layers = db.query(InventoryValuationLayer).filter(InventoryValuationLayer.company_id == company_id).delete(synchronize_session=False)
    mappings = db.query(StockProductMapping).filter(StockProductMapping.company_id == company_id).delete(synchronize_session=False)
    products = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "products")
        .delete(synchronize_session=False)
    )
    # Keep backfill disabled after a clear, don't re-enable it. New purchases
    # don't need backfill at all — sync_purchase_stock() (app_data.py) creates
    # their StockMovement/mapping directly at save time. Backfill only exists
    # to catch up orphaned/legacy purchase records that predate that sync —
    # exactly the records this wipe just deleted movements for. Previously
    # this removed the marker instead, which re-enabled backfill and had it
    # immediately resurrect every historical purchase's stock on the very
    # next GET, undoing the wipe.
    set_inventory_backfill_disabled(db, company_id)
    db.commit()
    return {
        "ok": True,
        "deleted": {
            "stock_movements": movements,
            "inventory_valuation_layers": layers,
            "stock_product_mappings": mappings,
            "products": products,
        },
    }


def inventory_backfill_disabled(db: Session, company_id: str) -> bool:
    marker = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "inventorySettings",
            AppDataRecord.record_key == "stock_backfill_disabled",
        )
        .first()
    )
    return bool(marker)


def set_inventory_backfill_disabled(db: Session, company_id: str) -> None:
    marker = (
        db.query(AppDataRecord)
        .filter(
            AppDataRecord.company_id == company_id,
            AppDataRecord.collection == "inventorySettings",
            AppDataRecord.record_key == "stock_backfill_disabled",
        )
        .first()
    )
    payload = json.dumps({"disabled": True, "reason": "Inventory stock table cleared by user"})
    if marker:
        marker.payload = payload
    else:
        db.add(AppDataRecord(company_id=company_id, collection="inventorySettings", record_key="stock_backfill_disabled", payload=payload))


def backfill_purchase_stock_movements(db: Session, principal: Principal) -> None:
    records = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == principal.company_id, AppDataRecord.collection == "purchaseRecords")
        .all()
    )
    if not records:
        return
    # This runs on every GET /inventory/stock-levels and /stock-movements
    # call (unless disabled) — was one existence-check query PER purchase
    # record, every single time, even when nothing had changed since the
    # last call. Fetch every reference that already has a movement once,
    # up front, instead.
    existing_refs = {
        row[0]
        for row in db.query(StockMovement.reference)
        .filter(
            StockMovement.company_id == principal.company_id,
            StockMovement.movement_type == "purchase",
        )
        .all()
    }
    changed = False
    updated_mapping_ids: set[int] = set()
    for item in records:
        try:
            record = json.loads(item.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if _is_demo_purchase_record(record):
            continue
        reference = str(record.get("ref") or record.get("invoice_no") or record.get("reference") or item.record_key or "").strip()
        if not reference:
            continue
        if reference in existing_refs:
            continue
        # Guard against two purchaseRecords sharing the same reference
        # within this same run — the old per-record query would've seen an
        # earlier iteration's not-yet-committed movement via autoflush.
        existing_refs.add(reference)
        lines = record.get("lines")
        if not isinstance(lines, list):
            continue
        # Prefer the purchase record's own stamped branch_id (Branch
        # Management Phase 4) over the current viewer's — a backfill run
        # triggered by one branch's GET shouldn't attribute EVERY orphaned
        # legacy purchase to that viewer's branch.
        branch_id = item.branch_id or principal.branch_id
        for line in lines:
            if not isinstance(line, dict):
                continue
            quantity = decimal_value(line.get("quantity") or line.get("qty") or line.get("purchase_qty") or line.get("qty_invoiced"))
            if quantity <= 0:
                continue
            mapping = stock_mapping_for_purchase_line(db, principal, record, line)
            if not mapping:
                continue
            if mapping.tracking in ("No", "Optional"):
                continue
            unit_cost = decimal_value(line.get("unit_cost_before_tax") or line.get("unit_cost") or line.get("purchase_unit_cost") or line.get("cost"))
            db.add(
                StockMovement(
                    company_id=principal.company_id,
                    branch_id=branch_id,
                    mapping_id=mapping.id,
                    movement_type="purchase",
                    quantity=quantity,
                    unit_cost=unit_cost,
                    reference=reference,
                )
            )
            db.add(
                InventoryValuationLayer(
                    company_id=principal.company_id,
                    item_code=mapping.sku,
                    source_module="purchase",
                    source_id=reference,
                    quantity_in=quantity,
                    quantity_remaining=quantity,
                    unit_cost=unit_cost,
                )
            )
            updated_mapping_ids.add(mapping.id)
            changed = True
    if changed:
        db.commit()
        # Recompute weighted average cost for each affected mapping
        _update_mapping_weighted_avg_cost(db, principal.company_id, updated_mapping_ids)
        db.commit()


def _update_mapping_weighted_avg_cost(db: Session, company_id: str, mapping_ids: set[int]) -> None:
    """Update each mapping's cost to the weighted average of all its purchase movements."""
    if not mapping_ids:
        return
    # Two batched queries instead of two per mapping_id — same movements/
    # mappings, same weighted-average math per mapping, just fetched once
    # for the whole set instead of once per mapping_id in the loop.
    movements_by_mapping: dict[int, list[StockMovement]] = {}
    for movement in (
        db.query(StockMovement)
        .filter(
            StockMovement.company_id == company_id,
            StockMovement.mapping_id.in_(mapping_ids),
            StockMovement.movement_type == "purchase",
            StockMovement.quantity > 0,
        )
        .all()
    ):
        movements_by_mapping.setdefault(movement.mapping_id, []).append(movement)
    if not movements_by_mapping:
        return
    mappings_by_id = {
        mapping.id: mapping
        for mapping in db.query(StockProductMapping).filter(
            StockProductMapping.id.in_(movements_by_mapping.keys()),
            StockProductMapping.company_id == company_id,
        ).all()
    }
    for mapping_id, movements in movements_by_mapping.items():
        total_qty = sum(float(m.quantity) for m in movements)
        total_value = sum(float(m.quantity) * float(m.unit_cost) for m in movements)
        if total_qty > 0:
            mapping = mappings_by_id.get(mapping_id)
            if mapping:
                mapping.cost = Decimal(str(round(total_value / total_qty, 6)))


def stock_mapping_for_purchase_line(
    db: Session,
    principal: Principal,
    record: dict[str, Any],
    line: dict[str, Any],
) -> StockProductMapping | None:
    sku = str(line.get("sku") or line.get("code") or "").strip()
    product = str(line.get("product") or line.get("name") or line.get("description") or "").strip()
    if not sku and not product:
        return None
    mapping = None
    # Case-insensitive lookup — prevents duplicate mappings for same item with different casing
    if sku:
        mapping = (
            db.query(StockProductMapping)
            .filter(
                StockProductMapping.company_id == principal.company_id,
                func.lower(StockProductMapping.sku) == sku.lower(),
            )
            .first()
        )
    if not mapping and product:
        mapping = (
            db.query(StockProductMapping)
            .filter(
                StockProductMapping.company_id == principal.company_id,
                func.lower(StockProductMapping.name) == product.lower(),
            )
            .first()
        )
    # Also try cross-matching sku↔name in case item was previously registered differently
    if not mapping and product:
        mapping = (
            db.query(StockProductMapping)
            .filter(
                StockProductMapping.company_id == principal.company_id,
                func.lower(StockProductMapping.sku) == product.lower(),
            )
            .first()
        )
    if not mapping and sku:
        mapping = (
            db.query(StockProductMapping)
            .filter(
                StockProductMapping.company_id == principal.company_id,
                func.lower(StockProductMapping.name) == sku.lower(),
            )
            .first()
        )
    if not mapping:
        # Same rule as purchase_line_stock_mapping() (app_data.py): a
        # low-confidence/error-flagged AI extraction shouldn't silently
        # mint a brand-new Inventory item from possibly-wrong product text.
        if record.get("needs_product_review"):
            return None
        mapping = StockProductMapping(
            company_id=principal.company_id,
            sku=sku or product[:60],
            name=product or sku,
            supplier_name=str(record.get("supplier") or "").strip() or None,
            mapping_confirmed=False,
        )
        db.add(mapping)
        db.flush()
    return mapping


def hydrate_mapping_costs_from_purchase_data(db: Session, company_id: str, mappings: list[StockProductMapping]) -> None:
    missing = [mapping for mapping in mappings if decimal_value(mapping.cost) == 0]
    if not missing:
        return
    costs = purchase_cost_lookup(db, company_id)
    changed = False
    for mapping in missing:
        cost = costs.get(normalize_key(mapping.sku)) or costs.get(normalize_key(mapping.name))
        if cost and cost > 0:
            mapping.cost = cost
            changed = True
    if changed:
        db.commit()


def purchase_cost_lookup(db: Session, company_id: str) -> dict[str, Decimal]:
    lookup: dict[str, Decimal] = {}
    rows = (
        db.query(AppDataRecord.collection, AppDataRecord.payload)
        .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(["products", "purchaseRecords"]))
        .all()
    )
    for collection, payload in rows:
        try:
            record = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        if collection == "products":
            add_cost_lookup(lookup, record.get("code") or record.get("sku"), record.get("cost") or record.get("purchase_price") or record.get("unit_cost"))
            add_cost_lookup(lookup, record.get("name"), record.get("cost") or record.get("purchase_price") or record.get("unit_cost"))
        else:
            for line in record.get("lines") or []:
                if not isinstance(line, dict):
                    continue
                cost = line.get("unit_cost") or line.get("purchase_unit_cost") or line.get("unit_cost_before_discount") or line.get("cost")
                add_cost_lookup(lookup, line.get("sku"), cost)
                add_cost_lookup(lookup, line.get("product") or line.get("description"), cost)
    return lookup


def add_cost_lookup(lookup: dict[str, Decimal], key: object, value: object) -> None:
    normalized = normalize_key(key)
    cost = decimal_value(value)
    if normalized and cost > 0 and normalized not in lookup:
        lookup[normalized] = cost


def normalize_key(value: object) -> str:
    return str(value or "").strip().lower()


def decimal_value(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


@router.post("/inventory/mappings", response_model=StockMappingOut, status_code=201)
def create_mapping(
    payload: StockMappingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StockProductMapping:
    mapping = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == payload.sku)
        .first()
    )
    if mapping:
        for field, value in payload.model_dump().items():
            setattr(mapping, field, value)
    else:
        mapping = StockProductMapping(company_id=current_user.company_id, **payload.model_dump())
        db.add(mapping)
    # Reaching this endpoint at all means a user explicitly saved it — not
    # client-controlled (not part of StockMappingIn), so it can't be spoofed
    # by payload content.
    mapping.mapping_confirmed = True
    db.commit()
    db.refresh(mapping)
    return mapping


@router.put("/inventory/mappings/{mapping_id}", response_model=StockMappingOut)
def update_mapping(
    mapping_id: str,
    payload: StockMappingIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StockProductMapping:
    mapping = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.id == mapping_id)
        .first()
    )
    if not mapping:
        raise HTTPException(status_code=404, detail="Stock mapping not found")
    for field, value in payload.model_dump().items():
        setattr(mapping, field, value)
    mapping.mapping_confirmed = True
    db.commit()
    db.refresh(mapping)
    return mapping


@router.delete("/inventory/mappings/{mapping_id}", status_code=204)
def delete_mapping(
    mapping_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    mapping = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.id == mapping_id)
        .first()
    )
    if not mapping:
        raise HTTPException(status_code=404, detail="Stock mapping not found")
    db.delete(mapping)
    db.commit()


@router.get("/item-units", response_model=list[ItemUnitOut])
def list_item_units(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[ItemUnit]:
    return db.query(ItemUnit).filter(ItemUnit.company_id == current_user.company_id).order_by(ItemUnit.item_code, ItemUnit.unit_code).all()


@router.post("/item-units", response_model=ItemUnitOut, status_code=201)
def create_item_unit(
    payload: ItemUnitIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemUnit:
    unit = (
        db.query(ItemUnit)
        .filter(
            ItemUnit.company_id == current_user.company_id,
            ItemUnit.item_code == payload.item_code,
            ItemUnit.unit_code == payload.unit_code,
        )
        .first()
    )
    if unit:
        unit.unit_name = payload.unit_name
        unit.conversion_factor = payload.conversion_factor
        unit.is_base_unit = payload.is_base_unit
        unit.purchase_default = payload.purchase_default
        unit.sales_default = payload.sales_default
        unit.status = payload.status
    else:
        unit = ItemUnit(company_id=current_user.company_id, **payload.model_dump())
        db.add(unit)
    db.commit()
    db.refresh(unit)
    return unit


@router.get("/item-unit-conversions", response_model=list[ItemUnitConversionOut])
def list_item_unit_conversions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ItemUnitConversion]:
    return (
        db.query(ItemUnitConversion)
        .filter(ItemUnitConversion.company_id == current_user.company_id)
        .order_by(ItemUnitConversion.item_code, ItemUnitConversion.from_unit_code, ItemUnitConversion.to_unit_code)
        .all()
    )


@router.post("/item-unit-conversions", response_model=ItemUnitConversionOut, status_code=201)
def create_item_unit_conversion(
    payload: ItemUnitConversionIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ItemUnitConversion:
    conversion = (
        db.query(ItemUnitConversion)
        .filter(
            ItemUnitConversion.company_id == current_user.company_id,
            ItemUnitConversion.item_code == payload.item_code,
            ItemUnitConversion.from_unit_code == payload.from_unit_code,
            ItemUnitConversion.to_unit_code == payload.to_unit_code,
        )
        .first()
    )
    if conversion:
        conversion.conversion_factor = payload.conversion_factor
        conversion.status = payload.status
    else:
        conversion = ItemUnitConversion(company_id=current_user.company_id, **payload.model_dump())
        db.add(conversion)
    db.commit()
    db.refresh(conversion)
    return conversion


@router.get("/inventory/valuation-layers", response_model=list[InventoryValuationLayerOut])
def list_valuation_layers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[InventoryValuationLayer]:
    return (
        db.query(InventoryValuationLayer)
        .filter(InventoryValuationLayer.company_id == current_user.company_id)
        .order_by(InventoryValuationLayer.created_at.desc())
        .all()
    )


@router.get("/inventory/adjustment-approvals", response_model=list[StockAdjustmentApprovalOut])
def list_adjustment_approvals(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[StockAdjustmentApproval]:
    return (
        db.query(StockAdjustmentApproval)
        .filter(StockAdjustmentApproval.company_id == current_user.company_id)
        .order_by(StockAdjustmentApproval.created_at.desc())
        .all()
    )


@router.post("/inventory/adjustment-approvals", response_model=StockAdjustmentApprovalOut, status_code=201)
def create_adjustment_approval(
    payload: StockAdjustmentApprovalIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StockAdjustmentApproval:
    approval = StockAdjustmentApproval(company_id=current_user.company_id, requested_by=current_user.id, **payload.model_dump())
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/inventory/adjustment-approvals/{approval_id}/approve", response_model=StockAdjustmentApprovalOut)
def approve_adjustment(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StockAdjustmentApproval:
    # Previously there was no approve/reject endpoint at all — creating an
    # approval request had zero effect on stock no matter what happened to
    # it afterward, since nothing ever read status=="approved" and applied
    # quantity_delta to real stock.
    approval = (
        db.query(StockAdjustmentApproval)
        .filter(StockAdjustmentApproval.company_id == current_user.company_id, StockAdjustmentApproval.id == approval_id)
        .first()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Adjustment approval not found")
    if approval.status == "approved":
        return approval
    mapping = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == approval.item_code)
        .first()
    )
    if not mapping:
        raise HTTPException(status_code=422, detail=f"No stock item found for code '{approval.item_code}'")
    unit_cost = mapping.cost or Decimal("0.00")
    db.add(
        StockMovement(
            company_id=current_user.company_id,
            mapping_id=mapping.id,
            warehouse_id=approval.warehouse_id,
            movement_type="adjustment",
            quantity=approval.quantity_delta,
            unit_cost=unit_cost,
            reference=f"ADJ-{approval.id[:8]}",
        )
    )
    if approval.quantity_delta < 0:
        consume_valuation_layers(db, current_user.company_id, approval.item_code, -approval.quantity_delta)
    else:
        db.add(
            InventoryValuationLayer(
                company_id=current_user.company_id,
                item_code=approval.item_code,
                warehouse_id=approval.warehouse_id,
                source_module="adjustment",
                source_id=approval.id,
                quantity_in=approval.quantity_delta,
                quantity_remaining=approval.quantity_delta,
                unit_cost=unit_cost,
            )
        )
    approval.status = "approved"
    approval.approved_by = current_user.id
    db.commit()
    db.refresh(approval)
    return approval


@router.post("/inventory/adjustment-approvals/{approval_id}/reject", response_model=StockAdjustmentApprovalOut)
def reject_adjustment(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StockAdjustmentApproval:
    approval = (
        db.query(StockAdjustmentApproval)
        .filter(StockAdjustmentApproval.company_id == current_user.company_id, StockAdjustmentApproval.id == approval_id)
        .first()
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Adjustment approval not found")
    if approval.status == "pending":
        approval.status = "rejected"
        approval.approved_by = current_user.id
        db.commit()
        db.refresh(approval)
    return approval
