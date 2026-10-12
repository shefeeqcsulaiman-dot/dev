"""A typed table as the main copy of JSON app-data records (step C of moving them out of JSON).

A record's known fields get columns; every other field goes into `extra` (JSON object text),
so the row alone holds the record and the JSON can be regenerated from it. Saves put the
record through its row (record_from(fields_from(doc))), so what is stored is what the row
gives back. app/parties.py (customers, suppliers) and app/catalog_products.py (products)
describe their fields with Field and use these.

Rules: a field given under its own name or an alias is kept even when empty ("" for text,
dropped for a number that isn't one); a text field is trimmed and clipped; money is a
number. The name is kept exactly as sent (it can be the record's key): a padded name goes
into extra so the regenerated record has it unchanged.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable


def money(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite() or abs(number) >= Decimal("1e12"):
        return None
    return number.quantize(Decimal("0.01"))


def digits(value: Any, size: int) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:size]


@dataclass(frozen=True)
class Field:
    name: str
    size: int | None = None            # text length; None for money
    aliases: tuple[str, ...] = ()      # other names the field may come as
    kind: str = "text"                 # text | money | digits


def _given(doc: dict[str, Any], field: Field) -> tuple[bool, Any]:
    """(the record has this field under its name or an alias, the first value that's set)."""
    keys = [key for key in (field.name, *field.aliases) if key in doc]
    if not keys:
        return False, None
    return True, next((doc[key] for key in keys if doc[key] not in (None, "")), doc[keys[0]])


def fields_from(doc: dict[str, Any], fields: Iterable[Field], skip: Iterable[str] = ()) -> dict[str, Any]:
    """Column values for one record, plus `extra`: its other fields, except those in `skip`
    (kept only in the JSON, e.g. an inline image)."""
    fields = tuple(fields)
    out: dict[str, Any] = {}
    for field in fields:
        present, value = _given(doc, field)
        if not present:
            out[field.name] = None
        elif field.kind == "money":
            out[field.name] = money(value) if value not in (None, "") else None
        elif field.kind == "digits":
            out[field.name] = digits(value, field.size or 40)
        else:
            out[field.name] = (str(value).strip() if value is not None else "")[:field.size]
    names = {field.name for field in fields}
    extra = {k: v for k, v in doc.items() if k not in names and k not in set(skip)}
    raw_name = doc.get("name")
    if "name" in names and isinstance(raw_name, str) and raw_name != (out["name"] or ""):
        extra["name"] = raw_name
    out["extra"] = json.dumps(extra, ensure_ascii=False, default=str) if extra else None
    return out


def record_from(source: Any, fields: Iterable[Field]) -> dict[str, Any]:
    """The JSON record regenerated from a row (or fields_from() output)."""
    get: Callable[[str], Any] = source.get if isinstance(source, dict) else (lambda key: getattr(source, key))
    record: dict[str, Any] = {}
    for field in fields:
        value = get(field.name)
        if value is not None:
            record[field.name] = float(value) if field.kind == "money" else value
    try:
        extra = json.loads(get("extra") or "{}")
    except (TypeError, ValueError):
        extra = {}
    record.update(extra if isinstance(extra, dict) else {})
    return record


def stale_json(session: Any, company_id: str, collections: Iterable[str], canonical: Callable[[dict], dict]) -> list[str]:
    """Ids of records whose stored JSON isn't what their row regenerates (written by a path
    that didn't go through the save, e.g. a restore, or saved before step C)."""
    from sqlalchemy import select

    from app.models import AppDataRecord

    stale = []
    for record_id, payload in session.execute(select(AppDataRecord.id, AppDataRecord.payload).where(
            AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(tuple(collections)))):
        try:
            doc = json.loads(payload or "{}")
        except (TypeError, ValueError):
            doc = None
        if not isinstance(doc, dict) or doc != canonical(doc):
            stale.append(record_id)
    return stale


def rewrite_json(session: Any, record_ids: Iterable[str], canonical: Callable[[dict], dict]) -> None:
    """Regenerate these records' JSON as their rows give it back."""
    from app.models import AppDataRecord

    ids = list(record_ids)
    for start in range(0, len(ids), 500):
        for row in session.query(AppDataRecord).filter(AppDataRecord.id.in_(ids[start:start + 500])):
            try:
                doc = json.loads(row.payload or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(doc, dict):
                row.payload = json.dumps(canonical(doc), ensure_ascii=False, default=str)


def check_tool(description: str, collections: Iterable[str], verify: Callable, canonical: Callable[[dict], dict],
               mirror: Any) -> None:
    """`python -m app.<module> [--rebuild] [--rewrite-json]` for a main-copy table."""
    import argparse

    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import AppDataRecord

    collections = tuple(collections)
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose rows differ")
    parser.add_argument("--rewrite-json", action="store_true", help="regenerate records whose JSON isn't canonical")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(collections)).distinct())]
        bad = [c for c in companies if not verify(db, c)]
        stale = {c: ids for c in companies if (ids := stale_json(db, c, collections, canonical))}
        print(f"{len(companies)} companies checked, {len(bad)} with rows that differ, "
              f"{sum(map(len, stale.values()))} records with non-canonical JSON")
        if args.rewrite_json and stale:
            for ids in stale.values():
                rewrite_json(db, ids, canonical)
            db.commit()
            print("JSON rewritten")
        if args.rebuild and bad:
            for company_id in bad:
                mirror.rebuild_company(db, company_id)
            db.commit()
            print(f"rebuilt {len(bad)}")
