"""Checks on products before they're saved (step B of moving them out of JSON, like
app/party_checks.py for customers and suppliers).

A product typed into a form (Item Master, the quick "new product" form, the row editor) is
refused with a clear message when it has no name, a code or barcode another product already
has (any case), or a price / stock level that isn't a number, is negative, or a minimum
stock above the maximum. Two products may share a name: the sales picker keeps same-name
products with different codes apart on purpose. An import (bulk save, or a single save
marked lenient, e.g. products created from AI-read purchase lines) never fails over one bad
field: the field is cleared and the product still saves; one whose code is another
product's in different letter case is skipped. Duplicates are found through the typed
catalog_products table (app/catalog_products.py).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import CatalogProduct

PRODUCT_COLLECTIONS = frozenset({"products"})
# Money and stock fields: a number, not negative, when given.
_NUMBER_FIELDS = {"cost": "Cost", "selling_price": "Selling price", "price": "Price",
                  "reorder_level": "Reorder level", "min_stock": "Minimum stock", "max_stock": "Maximum stock"}


def _number(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _clean(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Problems as (field, message); trims code and barcode in place (the name is left as sent)."""
    problems: list[tuple[str, str]] = []
    if not str(record.get("name") or "").strip():
        problems.append(("name", "Name is required."))
    for field in ("code", "barcode"):
        if isinstance(record.get(field), str):
            record[field] = record[field].strip()
    numbers = {}
    for field, label in _NUMBER_FIELDS.items():
        value = record.get(field)
        if value in (None, ""):
            continue
        number = _number(value)
        if number is None:
            problems.append((field, f"{label} must be a number."))
        elif number < 0:
            problems.append((field, f"{label} can't be negative."))
        else:
            numbers[field] = number
    if "min_stock" in numbers and "max_stock" in numbers and numbers["max_stock"] and numbers["min_stock"] > numbers["max_stock"]:
        problems.append(("min_stock", "Minimum stock is more than the maximum."))
    return problems


def _others(db: Session, company_id: str, key: str | None):
    """The company's other products: not the one being saved (keyed by its code, else name)."""
    query = db.query(CatalogProduct).filter(CatalogProduct.company_id == company_id)
    if key:
        query = query.filter(func.coalesce(CatalogProduct.record_key, "") != str(key).strip()[:160])
    return query


def check_product_form(db: Session, company_id: str, collection: str, record: dict[str, Any], key: str | None) -> None:
    """One save from a form: refuse with 422 (bad field) or 409 (code / barcode taken)."""
    if collection not in PRODUCT_COLLECTIONS:
        return
    problems = _clean(record)
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(message for _, message in problems))
    code = str(record.get("code") or "")
    if code:
        taken = _others(db, company_id, key).filter(CatalogProduct.code_key == code.lower()).first()
        if taken:
            raise HTTPException(status_code=409, detail=f"Item code \"{code}\" is already used by \"{taken.name}\".")
    barcode = str(record.get("barcode") or "")
    if barcode:
        taken = _others(db, company_id, key).filter(CatalogProduct.barcode == barcode).first()
        if taken:
            raise HTTPException(status_code=409, detail=f"Barcode {barcode} is already used by \"{taken.name}\".")


def clean_product_batch(db: Session, company_id: str, collection: str, records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """An import: bad numbers cleared, nameless products and case variants of another
    product's code skipped, a barcode another product has removed. Returns what to save."""
    if collection not in PRODUCT_COLLECTIONS:
        return records, {}
    code_of = {key: code for key, code in db.query(CatalogProduct.code_key, CatalogProduct.code).filter(
        CatalogProduct.company_id == company_id, CatalogProduct.code_key.isnot(None))}
    barcode_owner = {barcode: key for barcode, key in db.query(CatalogProduct.barcode, CatalogProduct.code_key).filter(
        CatalogProduct.company_id == company_id, CatalogProduct.barcode.isnot(None))}
    kept, counts = [], {"skipped": 0, "cleaned": 0}
    for record in records:
        problems = _clean(record)
        if any(field == "name" for field, _ in problems):
            counts["skipped"] += 1
            continue
        code = str(record.get("code") or "")
        key = code.lower()
        if code and key in code_of and code_of[key] != code:
            counts["skipped"] += 1  # "abc-1" when "ABC-1" exists: the same item, not a new one
            continue
        cleaned = False
        for field, _ in problems:
            record[field] = ""
            cleaned = True
        barcode = str(record.get("barcode") or "")
        if barcode and barcode_owner.get(barcode, key) != key:
            record["barcode"] = ""
            cleaned = True
        counts["cleaned"] += cleaned
        if code:
            code_of[key] = code
        if record.get("barcode"):
            barcode_owner[record["barcode"]] = key
        kept.append(record)
    return kept, counts
