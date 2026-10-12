"""Products as real rows (the catalog_products table): the main copy of each product.

Every product has one row here with its fields as columns (code, name, type, category,
unit, selling price, cost, VAT, tracking, supplier, barcode, status) and any other fields
in `extra`, so the row holds the whole product except its inline image, which stays only
in the JSON (as a file store would hold it; copying it here would store each image twice).
Saves go through the row: the JSON app-data record is regenerated from it plus the image
(canonical_product()), and kept because many generic readers still read app_data_records.
Step A made this a copy; step B checks saves (app/product_checks.py); step C (this) makes
the row the source. The picker / list search (GET /app-data/catalog/products) reads it.
Check, rebuild or rewrite with `python -m app.catalog_products [--rebuild] [--rewrite-json]`.

Rules (app/main_copy.py): code and name get lower-cased search keys, prices are numbers,
a field given but empty is kept as "". app.js saves the selling price as price (quick-add
form) or selling_price (Item Master): both are this one field.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, CatalogProduct
from app import main_copy
from app.main_copy import Field, fields_from, record_from
from app.parties import _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("products",)


FIELDS = (Field("name", 255), Field("code", 120, ("sku",)), Field("type", 60), Field("category", 120),
          Field("unit", 40), Field("selling_price", kind="money", aliases=("price", "sales_price", "unit_price")),
          Field("cost", kind="money", aliases=("unit_cost",)), Field("vat", 40), Field("tracking", 20),
          Field("supplier_name", 255, ("supplier",)), Field("barcode", 80), Field("status", 40))
IMAGE = "image"  # kept only in the JSON


def product_fields(doc: dict[str, Any]) -> dict[str, Any]:
    """Column values for one product, plus `extra` (its other fields, the image aside)."""
    return fields_from(doc, FIELDS, skip=(IMAGE,))


def product_record(fields: Any, image: Any = None) -> dict[str, Any]:
    """The JSON record regenerated from a catalog_products row (or product_fields() output)."""
    record = record_from(fields, FIELDS)
    if image is not None:
        record[IMAGE] = image
    return record


def canonical_product(record: dict[str, Any]) -> dict[str, Any]:
    """A product as it is stored: put through its row and back, its image kept."""
    return product_record(product_fields(record), record.get(IMAGE) if IMAGE in record else None)


def product_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = product_fields(doc)
    name, code = fields["name"] or "", fields["code"] or None
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        **fields,
        "code": code,
        "code_key": code.lower() if code else None,
        "name": name,
        "name_key": name.lower(),
    }]


mirror = RecordMirror("catalog_products", COLLECTIONS, CatalogProduct, CatalogProduct.id, product_rows).register()

_COMPARED = ("record_key", "code_key", "name_key", *(f.name for f in FIELDS), "extra")


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's catalog_products rows match what its JSON records produce."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(CatalogProduct).filter(CatalogProduct.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(COLLECTIONS))):
        for row in product_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def stale_json(session: Session, company_id: str) -> list[str]:
    return main_copy.stale_json(session, company_id, COLLECTIONS, canonical_product)


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    main_copy.rewrite_json(session, record_ids, canonical_product)


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the catalog_products table and the JSON it regenerates.", COLLECTIONS, verify_company, canonical_product, mirror)


if __name__ == "__main__":
    _main()
