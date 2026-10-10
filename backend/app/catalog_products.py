"""Products as real rows (the catalog_products table), step A of moving them out of JSON.

Every save, edit and delete of a "products" app-data record updates its row here
(app/record_mirror.py), like app/parties.py does for customers and vendors. The JSON
record is still the source of truth; the rows can be checked or rebuilt with
`python -m app.catalog_products [--rebuild]`. The picker / list search
(GET /app-data/catalog/products) reads from this table.

Values are normalised on the way in: code and name get lower-cased search keys, prices are
numbers (or empty when they aren't one). Field names follow what app.js saves (Item Master
and the quick-add product form: price and selling_price both mean the selling price).
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, CatalogProduct
from app.parties import _money, _text
from app.record_mirror import Record, RecordMirror

COLLECTIONS = ("products",)


def _first(doc: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = doc.get(key)
        if value not in (None, ""):
            return value
    return None


def product_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    name = _text(doc.get("name"), 255) or ""
    code = _text(_first(doc, "code", "sku") or record_key, 120)
    price = _first(doc, "selling_price", "price", "sales_price", "unit_price")
    cost = _first(doc, "cost", "unit_cost")
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        "code": code,
        "code_key": code.lower() if code else None,
        "name": name,
        "name_key": name.lower(),
        "type": _text(doc.get("type"), 60),
        "category": _text(doc.get("category"), 120),
        "unit": _text(doc.get("unit"), 40),
        "selling_price": _money(price) if price is not None else None,
        "cost": _money(cost) if cost is not None else None,
        "vat": _text(doc.get("vat"), 40),
        "tracking": _text(doc.get("tracking"), 20),
        "supplier_name": _text(_first(doc, "supplier_name", "supplier"), 255),
        "barcode": _text(doc.get("barcode"), 80),
        "status": _text(doc.get("status"), 40),
    }]


mirror = RecordMirror("catalog_products", COLLECTIONS, CatalogProduct, CatalogProduct.id, product_rows).register()

_COMPARED = ("code", "name", "unit", "selling_price", "cost", "supplier_name", "status", "record_key")


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


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) the catalog_products table against the JSON records.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(COLLECTIONS)).distinct())]
        bad = [c for c in companies if not verify_company(db, c)]
        print(f"{len(companies)} companies checked, {len(bad)} differ")
        if args.rebuild and bad:
            for company_id in bad:
                mirror.rebuild_company(db, company_id)
            db.commit()
            print(f"rebuilt {len(bad)}")


if __name__ == "__main__":
    _main()
