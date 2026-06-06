import json
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
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


router = APIRouter(tags=["inventory"])


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
def list_mappings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[StockProductMapping]:
    mappings = (
        db.query(StockProductMapping)
        .filter(StockProductMapping.company_id == current_user.company_id)
        .order_by(StockProductMapping.sku)
        .all()
    )
    hydrate_mapping_costs_from_purchase_data(db, current_user.company_id, mappings)
    return mappings


@router.get("/inventory/stock-levels")
def list_stock_levels(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)) -> list[dict[str, object]]:
    if not inventory_backfill_disabled(db, current_user.company_id):
        backfill_purchase_stock_movements(db, current_user)
    rows = (
        db.query(
            StockProductMapping,
            func.coalesce(func.sum(StockMovement.quantity), 0).label("current_stock"),
        )
        .outerjoin(
            StockMovement,
            (StockMovement.mapping_id == StockProductMapping.id)
            & (StockMovement.company_id == current_user.company_id),
        )
        .filter(StockProductMapping.company_id == current_user.company_id)
        .group_by(StockProductMapping.id)
        .order_by(StockProductMapping.sku)
        .all()
    )
    return [
        {
            "code": mapping.sku,
            "name": mapping.taxflow_name or mapping.name,
            "category": "Purchases",
            "current_stock": current_stock,
            "unit": "PCS",
            "reorder_level": mapping.reorder_level,
            "cost": mapping.cost,
        }
        for mapping, current_stock in rows
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


def backfill_purchase_stock_movements(db: Session, current_user: User) -> None:
    records = (
        db.query(AppDataRecord)
        .filter(AppDataRecord.company_id == current_user.company_id, AppDataRecord.collection == "purchaseRecords")
        .all()
    )
    changed = False
    for item in records:
        try:
            record = json.loads(item.payload or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        reference = str(record.get("ref") or record.get("invoice_no") or record.get("reference") or item.record_key or "").strip()
        if not reference:
            continue
        existing = (
            db.query(StockMovement.id)
            .filter(
                StockMovement.company_id == current_user.company_id,
                StockMovement.movement_type == "purchase",
                StockMovement.reference == reference,
            )
            .first()
        )
        if existing:
            continue
        lines = record.get("lines")
        if not isinstance(lines, list):
            continue
        for line in lines:
            if not isinstance(line, dict):
                continue
            quantity = decimal_value(line.get("quantity") or line.get("qty") or line.get("purchase_qty") or line.get("qty_invoiced"))
            if quantity <= 0:
                continue
            mapping = stock_mapping_for_purchase_line(db, current_user, record, line)
            if not mapping:
                continue
            unit_cost = decimal_value(line.get("unit_cost_before_tax") or line.get("unit_cost") or line.get("purchase_unit_cost") or line.get("cost"))
            db.add(
                StockMovement(
                    company_id=current_user.company_id,
                    mapping_id=mapping.id,
                    movement_type="purchase",
                    quantity=quantity,
                    unit_cost=unit_cost,
                    reference=reference,
                )
            )
            db.add(
                InventoryValuationLayer(
                    company_id=current_user.company_id,
                    item_code=mapping.sku,
                    source_module="purchase",
                    source_id=reference,
                    quantity_in=quantity,
                    quantity_remaining=quantity,
                    unit_cost=unit_cost,
                )
            )
            changed = True
    if changed:
        db.commit()


def stock_mapping_for_purchase_line(
    db: Session,
    current_user: User,
    record: dict[str, Any],
    line: dict[str, Any],
) -> StockProductMapping | None:
    sku = str(line.get("sku") or line.get("code") or "").strip()
    product = str(line.get("product") or line.get("name") or line.get("description") or "").strip()
    if not sku and not product:
        return None
    mapping = None
    if sku:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.sku == sku)
            .first()
        )
    if not mapping and product:
        mapping = (
            db.query(StockProductMapping)
            .filter(StockProductMapping.company_id == current_user.company_id, StockProductMapping.name == product)
            .first()
        )
    if not mapping:
        mapping = StockProductMapping(
            company_id=current_user.company_id,
            sku=sku or product[:60],
            name=product or sku,
            supplier_name=str(record.get("supplier") or "").strip() or None,
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
