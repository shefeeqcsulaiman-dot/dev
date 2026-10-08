"""Line items of JSON app-data documents as real rows (document_lines).

Step 3 of moving these records out of JSON (docs/scaling-plan-10k.md): each sales
invoice's "lines" are written to document_lines whenever the invoice is saved, so
line-level reads can be SQL queries instead of decoding every invoice the company
has. Used today by the register's product filter ("invoices containing this
product"). The JSON record is still the source of truth; these rows are rebuilt from
it and can always be rebuilt again (`python -m app.doc_lines --rebuild`).

Measured on 5,000 invoices / 25,000 lines (SQLite): the product filter is ~30%
faster. Returning every line one row per line (the stock movements endpoint, which
sends a company's whole history) was slower than parsing the JSON, so that endpoint
still reads the JSON until its screen filters and pages on the server.

Kept current for every kind of write to app_data_records:
- ORM saves, edits and deletes: after each flush, the touched records' lines are
  replaced (or removed).
- Bulk query().delete(): the matching records' lines are deleted first, in the same
  transaction.
- Bulk update() of payload/collection, and bulk inserts: the records are refreshed
  before commit.
Readers join document_lines to app_data_records, so a line whose record is gone
never shows even if something slipped past these hooks.
"""
from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from sqlalchemy import and_, delete, event, exists, inspect, insert, select
from sqlalchemy.orm import Session

from app.models import AppDataRecord, DocumentLine

LINE_COLLECTIONS = frozenset({"salesInvoices"})

_BUSY = "_doc_lines_busy"
_REFRESH = "_doc_lines_refresh"        # record ids whose lines to rebuild after the flush
_DROP = "_doc_lines_drop"              # record ids whose lines to delete after the flush
_BULK_REFRESH = "_doc_lines_bulk"      # record ids touched by bulk update(), rebuilt before commit
_BULK_COMPANIES = "_doc_lines_bulk_co"  # companies with bulk-inserted records, filled before commit


def _clip(value: Any, size: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:size] if text else None


def _key(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value).lower()[:255]


def _number(value: Any, places: str | None = None) -> Decimal | None:
    try:
        number = Decimal(str(float(value)))
    except (TypeError, ValueError, InvalidOperation, OverflowError):
        return None
    if not number.is_finite():
        return None
    return number.quantize(Decimal(places)) if places else number


def line_rows(company_id: str, record_id: str, collection: str, payload: str | None) -> list[dict[str, Any]]:
    """document_lines rows for one record's saved JSON."""
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list):
        return []
    doc_date = doc.get("date")
    head = {
        "company_id": company_id,
        "record_id": record_id,
        "collection": collection,
        "doc_date": _clip(doc_date, 40) if doc_date else None,
        "doc_ref": _clip(doc.get("invoice_no"), 160),
        "doc_source": _key(doc.get("source"))[:40] if doc.get("source") else None,
    }
    rows = []
    for line_no, line in enumerate(doc["lines"]):
        if not isinstance(line, dict):
            continue
        qty = line.get("qty") or line.get("quantity") or 0
        price = line.get("unit_price") if line.get("unit_price") not in (None, "") else line.get("price")
        total = next((line.get(k) for k in ("amount", "total", "line_total") if line.get(k) not in (None, "")), None)
        rows.append({
            **head,
            "line_no": line_no,
            "item_name": _clip(str(line.get("description") or line.get("product") or "").strip(), 255),
            "description_key": _key(line.get("description")),
            "product_name_key": _key(line.get("product_name")),
            "product_key": _key(line.get("product")),
            "product_code": _clip(line.get("product_code"), 80),
            "unit": _clip(line.get("unit"), 40),
            "quantity": _number(qty),
            "unit_price": _number(price, "0.01"),
            "line_total": _number(total, "0.01"),
        })
    return rows


def _replace(session: Session, records: Iterable[tuple[str, str, str, str | None]]) -> None:
    """(record_id, company_id, collection, payload) -> delete the old lines, insert new."""
    records = list(records)
    if not records:
        return
    session.info[_BUSY] = True
    try:
        ids = [r[0] for r in records]
        for start in range(0, len(ids), 500):
            session.execute(delete(DocumentLine).where(DocumentLine.record_id.in_(ids[start:start + 500])))
        rows = [row for rid, cid, coll, payload in records if coll in LINE_COLLECTIONS for row in line_rows(cid, rid, coll, payload)]
        for start in range(0, len(rows), 1000):
            session.execute(insert(DocumentLine), rows[start:start + 1000])
    finally:
        session.info.pop(_BUSY, None)


def _drop(session: Session, record_ids: Iterable[str]) -> None:
    ids = list(record_ids)
    if not ids:
        return
    session.info[_BUSY] = True
    try:
        for start in range(0, len(ids), 500):
            session.execute(delete(DocumentLine).where(DocumentLine.record_id.in_(ids[start:start + 500])))
    finally:
        session.info.pop(_BUSY, None)


def rebuild_company(session: Session, company_id: str) -> int:
    """Rebuild every line of a company's line collections from their JSON. Returns the
    number of records read."""
    session.info[_BUSY] = True
    try:
        session.execute(delete(DocumentLine).where(DocumentLine.company_id == company_id))
    finally:
        session.info.pop(_BUSY, None)
    count = 0
    last_id = ""
    while True:  # keyset batches, so memory stays flat for a big company
        batch = [tuple(r) for r in session.execute(
            select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload)
            .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(LINE_COLLECTIONS),
                   AppDataRecord.id > last_id)
            .order_by(AppDataRecord.id)
            .limit(500)
        ).all()]
        if not batch:
            return count
        _insert_only(session, batch)
        count += len(batch)
        last_id = batch[-1][0]


def _insert_only(session: Session, records: list[tuple[str, str, str, str | None]]) -> None:
    rows = [row for rid, cid, coll, payload in records for row in line_rows(cid, rid, coll, payload)]
    if not rows:
        return
    session.info[_BUSY] = True
    try:
        for start in range(0, len(rows), 1000):
            session.execute(insert(DocumentLine), rows[start:start + 1000])
    finally:
        session.info.pop(_BUSY, None)


def verify_company(session: Session, company_id: str) -> bool:
    """True when the stored lines match what the JSON records give."""
    stored: dict[str, list[tuple]] = {}
    cols = (DocumentLine.record_id, DocumentLine.line_no, DocumentLine.item_name, DocumentLine.quantity,
            DocumentLine.unit_price, DocumentLine.line_total, DocumentLine.doc_ref)
    for rid, *rest in session.execute(select(*cols).where(DocumentLine.company_id == company_id)):
        stored.setdefault(rid, []).append(tuple(rest))
    expected: dict[str, list[tuple]] = {}
    for rid, cid, coll, payload in session.execute(
            select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload)
            .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(LINE_COLLECTIONS))):
        rows = line_rows(cid, rid, coll, payload)
        if rows:
            expected[rid] = [(r["line_no"], r["item_name"], r["quantity"], r["unit_price"], r["line_total"], r["doc_ref"]) for r in rows]

    def norm(lines):
        return sorted((n, i, None if q is None else Decimal(q).normalize(), p, t, ref) for n, i, q, p, t, ref in lines)

    return {k: norm(v) for k, v in stored.items()} == {k: norm(v) for k, v in expected.items()}


# ── keeping the lines current ────────────────────────────────────────────────

def _changed(obj: AppDataRecord, attr: str) -> bool:
    return bool(inspect(obj).attrs[attr].history.has_changes())


@event.listens_for(Session, "after_flush")
def _collect(session: Session, _ctx) -> None:
    if session.info.get(_BUSY):
        return
    refresh: dict[str, AppDataRecord] = session.info.setdefault(_REFRESH, {})
    drop: set[str] = session.info.setdefault(_DROP, set())
    for obj in session.new:
        if isinstance(obj, AppDataRecord) and obj.collection in LINE_COLLECTIONS:
            refresh[obj.id] = obj
    for obj in session.dirty:
        if not isinstance(obj, AppDataRecord):
            continue
        if not (_changed(obj, "payload") or _changed(obj, "collection") or _changed(obj, "company_id")):
            continue
        if obj.collection in LINE_COLLECTIONS:
            refresh[obj.id] = obj
        else:
            drop.add(obj.id)  # moved out of a line collection
    for obj in session.deleted:
        if isinstance(obj, AppDataRecord):
            drop.add(obj.id)


@event.listens_for(Session, "after_flush_postexec")
def _apply(session: Session, _ctx) -> None:
    if session.info.get(_BUSY):
        return
    refresh: dict[str, AppDataRecord] = session.info.pop(_REFRESH, {})
    drop: set[str] = session.info.pop(_DROP, set())
    _drop(session, drop - set(refresh))
    _replace(session, ((obj.id, obj.company_id, obj.collection, obj.payload) for obj in refresh.values()))


def _statement_columns(stmt: Any) -> set[str] | None:
    """Column names an UPDATE sets, or None when they can't be read (treat as all)."""
    names: set[str] = set()
    for attr in ("_values", "_ordered_values"):
        values = getattr(stmt, attr, None)
        if not values:
            continue
        items = values.items() if hasattr(values, "items") else values
        for key, _ in items:
            name = getattr(key, "key", None) or getattr(key, "name", None) or (key if isinstance(key, str) else None)
            if name is None:
                return None
            names.add(name)
    return names or None


@event.listens_for(Session, "do_orm_execute")
def _bulk(state) -> None:
    session = state.session
    if session.info.get(_BUSY) or not (state.is_delete or state.is_update or state.is_insert):
        return
    mapper = state.bind_mapper
    if mapper is None or mapper.class_ is not AppDataRecord:
        return
    stmt = state.statement
    if state.is_insert:
        params = state.parameters if isinstance(state.parameters, list) else [state.parameters or {}]
        session.info.setdefault(_BULK_COMPANIES, set()).update(
            p.get("company_id") for p in params if p.get("company_id") and p.get("collection") in LINE_COLLECTIONS
        )
        return
    where = stmt.whereclause
    if state.is_delete:
        # Delete the lines of exactly the records this statement is about to delete.
        ids = select(AppDataRecord.id)
        if where is not None:
            ids = ids.where(where)
        session.info[_BUSY] = True
        try:
            with session.no_autoflush:
                session.execute(delete(DocumentLine).where(DocumentLine.record_id.in_(ids)))
        finally:
            session.info.pop(_BUSY, None)
        return
    columns = _statement_columns(stmt)
    if columns is not None and not columns & {"payload", "collection", "company_id"}:
        return
    query = select(AppDataRecord.id)
    if where is not None:
        query = query.where(where)
    with session.no_autoflush:
        session.info.setdefault(_BULK_REFRESH, set()).update(session.execute(query).scalars())


@event.listens_for(Session, "before_commit")
def _before_commit(session: Session) -> None:
    if session.info.get(_BUSY):
        return
    ids: set[str] = session.info.pop(_BULK_REFRESH, set())
    companies: set[str] = session.info.pop(_BULK_COMPANIES, set())
    if not ids and not companies:
        return
    session.flush()
    if ids:
        rows = session.execute(
            select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload)
            .where(AppDataRecord.id.in_(ids))
        ).all()
        _drop(session, ids - {r[0] for r in rows})
        _replace(session, [tuple(r) for r in rows])
    for company_id in companies:
        # Bulk-inserted records: fill those that have no lines yet.
        missing = session.execute(
            select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload)
            .where(
                AppDataRecord.company_id == company_id,
                AppDataRecord.collection.in_(LINE_COLLECTIONS),
                ~exists().where(and_(DocumentLine.record_id == AppDataRecord.id)),
            )
        ).all()
        _insert_only(session, [tuple(r) for r in missing])


@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    for key in (_REFRESH, _DROP, _BULK_REFRESH, _BULK_COMPANIES):
        session.info.pop(key, None)


def _main() -> None:
    import argparse

    from app.database import SessionLocal

    parser = argparse.ArgumentParser(description="Check (or rebuild) document_lines against the JSON records.")
    parser.add_argument("--rebuild", action="store_true", help="rebuild companies whose lines differ")
    args = parser.parse_args()
    with SessionLocal() as db:
        companies = [c for (c,) in db.execute(
            select(AppDataRecord.company_id).where(AppDataRecord.collection.in_(LINE_COLLECTIONS)).distinct())]
        bad = [c for c in companies if not verify_company(db, c)]
        print(f"{len(companies)} companies checked, {len(bad)} differ")
        if args.rebuild:
            for company_id in bad:
                rebuild_company(db, company_id)
            db.commit()
            print(f"rebuilt {len(bad)}")


if __name__ == "__main__":
    _main()
