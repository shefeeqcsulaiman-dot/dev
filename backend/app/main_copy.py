"""A typed table as the main copy of JSON app-data records (step C of moving them out of JSON).

A record's known fields get columns; every other field goes into `extra` (JSON object text),
so the row alone holds the record and the JSON can be regenerated from it. Saves put the
record through its row (record_from(fields_from(doc))), so what is stored is what the row
gives back. app/parties.py (customers, suppliers) and app/catalog_products.py (products)
describe their fields with Field and use these.

Rules: a field given under its own name or an alias is kept even when empty. Its column is
normalised: text trimmed and clipped, money a number, digits only for a TRN, a day as
YYYY-MM-DD, a percentage a whole number 0-100. In the JSON, text keeps its original value
(type and spacing: a name can be the record's key, an id can be a number other code
compares exactly), so a text value that differs from its column goes into extra; money,
digits, days and percentages are stored normalised (one that isn't valid is dropped, or ""
for a day).
"""
from __future__ import annotations

import json
import re
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


def amount(value: Any) -> float | int | None:
    """A document amount as a number at full precision ("1,234.50" -> 1234.5; 95.2380952
    stays as it is, so VAT and totals don't move); None when it isn't a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value if value == value and abs(value) != float("inf") else None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return int(number) if number == number.to_integral_value() and "." not in str(value) else float(number)


def digits(value: Any, size: int) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:size]


_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def day(value: Any) -> str:
    text = str(value or "").strip()[:10]
    return text if _ISO_DAY.match(text) else ""


def minutes(value: Any) -> float | int | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN is not a number of minutes
        return None
    number = max(0.0, number)
    return int(number) if number.is_integer() else number


def percent(value: Any) -> int | None:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, min(100, number))


@dataclass(frozen=True)
class Field:
    name: str                          # the record's (JSON) field name
    size: int | None = None            # text length; None for money
    aliases: tuple[str, ...] = ()      # other names the field may come as
    kind: str = "text"                 # text | money | digits | day | percent | minutes | amount | number
    column: str | None = None          # the table column, when it has another name

    @property
    def col(self) -> str:
        return self.column or self.name


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
    names = {field.name for field in fields}
    extra = {k: v for k, v in doc.items() if k not in names and k not in set(skip)}
    for field in fields:
        present, value = _given(doc, field)
        col = field.col
        if not present:
            out[col] = None
        elif field.kind == "money":
            out[col] = money(value) if value not in (None, "") else None
        elif field.kind == "digits":
            out[col] = digits(value, field.size or 40)
        elif field.kind == "day":
            out[col] = day(value)
        elif field.kind == "percent":
            out[col] = percent(value) if value not in (None, "") else None
        elif field.kind == "minutes":
            out[col] = minutes(value) if value not in (None, "") else None
        elif field.kind == "amount":
            out[col] = amount(value) if value not in (None, "") else None
            if out[col] is None and field.name in doc and doc[field.name] is not None:
                extra[field.name] = doc[field.name]  # not a number: kept exactly as given
        elif field.kind == "number":
            # A number column; the JSON keeps the value exactly as given (e.g. hours saved as
            # the text "2.5", which code reading the record may expect).
            out[col] = amount(value) if value not in (None, "") else None
            if field.name in doc and (doc[field.name] != out[col] or type(doc[field.name]) is not type(out[col])):
                extra[field.name] = doc[field.name]
        else:
            out[col] = (str(value).strip() if value is not None else "")[:field.size]
            if field.name in doc and doc[field.name] != out[col]:
                extra[field.name] = doc[field.name]  # the JSON keeps the value exactly as given
    out["extra"] = json.dumps(extra, ensure_ascii=False, default=str) if extra else None
    return out


def record_from(source: Any, fields: Iterable[Field]) -> dict[str, Any]:
    """The JSON record regenerated from a row (or fields_from() output)."""
    get: Callable[[str], Any] = source.get if isinstance(source, dict) else (lambda key: getattr(source, key))
    record: dict[str, Any] = {}
    for field in fields:
        value = get(field.col)
        if value is not None:
            if field.kind == "money":
                value = float(value)
            elif isinstance(value, Decimal):  # a numeric column read back from the database
                value = int(value) if value == value.to_integral_value() else float(value)
            record[field.name] = value
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
