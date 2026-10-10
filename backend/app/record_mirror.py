"""Keeps a table of typed rows in step with JSON app-data records, for every kind of write.

Moving records out of JSON (docs/scaling-plan-10k.md) happens in steps. Step A for a
collection: its records get real columns in their own table, derived from the JSON on
every save, edit and delete, so the app keeps working unchanged while reads move over.
app/doc_lines.py does this for document lines; RecordMirror is the same machinery made
reusable (app/parties.py uses it for customers and vendors).

Covered writes to app_data_records (same as doc_lines):
- ORM saves, edits and deletes: after each flush the touched records' rows are replaced
  (or removed).
- Bulk query().delete(): the matching records' rows are deleted first, same transaction.
- Bulk update() of payload/collection/company, and bulk inserts: refreshed before commit.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

import sqlalchemy as sa
from sqlalchemy import and_, delete, event, exists, inspect, insert, select
from sqlalchemy.orm import Session

from app.models import AppDataRecord

# (record id, company id, collection, payload, branch id, record key)
Record = tuple[str, str, str, "str | None", "str | None", "str | None"]
RowsFor = Callable[[Record], list[dict[str, Any]]]

_SOURCE = (AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.payload,
           AppDataRecord.branch_id, AppDataRecord.record_key)


class RecordMirror:
    def __init__(self, name: str, collections: Iterable[str], model: Any, record_column: Any, rows_for: RowsFor):
        self.collections = frozenset(collections)
        self.model = model
        self.record_column = record_column  # the column holding the app_data_records id
        self.rows_for = rows_for
        self._busy = f"_{name}_busy"
        self._refresh = f"_{name}_refresh"
        self._drop = f"_{name}_drop"
        self._bulk_refresh = f"_{name}_bulk"
        self._bulk_companies = f"_{name}_bulk_co"

    # ── writing rows ─────────────────────────────────────────────────────────

    def _guarded(self, session: Session, fn: Callable[[], None]) -> None:
        session.info[self._busy] = True
        try:
            fn()
        finally:
            session.info.pop(self._busy, None)

    def drop(self, session: Session, record_ids: Iterable[str]) -> None:
        ids = list(record_ids)
        if not ids:
            return

        def run():
            for start in range(0, len(ids), 500):
                session.execute(delete(self.model).where(self.record_column.in_(ids[start:start + 500])))
        self._guarded(session, run)

    def insert_only(self, session: Session, records: Iterable[Record]) -> None:
        rows = [row for rec in records if rec[2] in self.collections for row in self.rows_for(rec)]
        if not rows:
            return

        def run():
            for start in range(0, len(rows), 1000):
                session.execute(insert(self.model), rows[start:start + 1000])
        self._guarded(session, run)

    def replace(self, session: Session, records: Iterable[Record]) -> None:
        records = list(records)
        if not records:
            return
        self.drop(session, [r[0] for r in records])
        self.insert_only(session, records)

    def rebuild_company(self, session: Session, company_id: str) -> int:
        """Rebuild a company's rows from its JSON records. Returns the number read."""
        self._guarded(session, lambda: session.execute(delete(self.model).where(self.model.company_id == company_id)))
        count, last_id = 0, ""
        while True:  # keyset batches: memory stays flat for a big company
            batch = [tuple(r) for r in session.execute(
                select(*_SOURCE).where(AppDataRecord.company_id == company_id,
                                       AppDataRecord.collection.in_(self.collections), AppDataRecord.id > last_id)
                .order_by(AppDataRecord.id).limit(500)).all()]
            if not batch:
                return count
            self.insert_only(session, batch)
            count += len(batch)
            last_id = batch[-1][0]

    def refill_all(self, bind: Any, table: Any, batch: int = 1000) -> None:
        """Fill `table` (a lightweight sa.table for migrations) from every record, on a Connection."""
        records = sa.table("app_data_records", *[sa.column(c.key) for c in _SOURCE])
        last_id = ""
        while True:
            rows = bind.execute(
                select(*[records.c[c.key] for c in _SOURCE])
                .where(records.c.collection.in_(self.collections), records.c.id > last_id)
                .order_by(records.c.id).limit(batch)).all()
            if not rows:
                return
            out = [row for rec in rows for row in self.rows_for(tuple(rec))]
            if out:
                bind.execute(insert(table), out)
            last_id = rows[-1][0]

    # ── session hooks ────────────────────────────────────────────────────────

    def register(self) -> "RecordMirror":
        event.listen(Session, "after_flush", self._collect)
        event.listen(Session, "after_flush_postexec", self._apply)
        event.listen(Session, "do_orm_execute", self._bulk)
        event.listen(Session, "before_commit", self._before_commit)
        event.listen(Session, "after_rollback", self._forget)
        return self

    @staticmethod
    def _changed(obj: AppDataRecord, attr: str) -> bool:
        return bool(inspect(obj).attrs[attr].history.has_changes())

    def _collect(self, session: Session, _ctx) -> None:
        if session.info.get(self._busy):
            return
        refresh: dict[str, AppDataRecord] = session.info.setdefault(self._refresh, {})
        drop: set[str] = session.info.setdefault(self._drop, set())
        for obj in session.new:
            if isinstance(obj, AppDataRecord) and obj.collection in self.collections:
                refresh[obj.id] = obj
        for obj in session.dirty:
            if not isinstance(obj, AppDataRecord):
                continue
            if not any(self._changed(obj, a) for a in ("payload", "collection", "company_id", "branch_id", "record_key")):
                continue
            if obj.collection in self.collections:
                refresh[obj.id] = obj
            else:
                drop.add(obj.id)  # moved out of a mirrored collection
        for obj in session.deleted:
            if isinstance(obj, AppDataRecord):
                drop.add(obj.id)

    def _apply(self, session: Session, _ctx) -> None:
        if session.info.get(self._busy):
            return
        refresh: dict[str, AppDataRecord] = session.info.pop(self._refresh, {})
        drop: set[str] = session.info.pop(self._drop, set())
        self.drop(session, drop - set(refresh))
        self.replace(session, ((o.id, o.company_id, o.collection, o.payload, o.branch_id, o.record_key)
                               for o in refresh.values()))

    def _bulk(self, state) -> None:
        session = state.session
        if session.info.get(self._busy) or not (state.is_delete or state.is_update or state.is_insert):
            return
        mapper = state.bind_mapper
        if mapper is None or mapper.class_ is not AppDataRecord:
            return
        stmt = state.statement
        if state.is_insert:
            params = state.parameters if isinstance(state.parameters, list) else [state.parameters or {}]
            session.info.setdefault(self._bulk_companies, set()).update(
                p.get("company_id") for p in params if p.get("company_id") and p.get("collection") in self.collections)
            return
        where = stmt.whereclause
        if state.is_delete:
            ids = select(AppDataRecord.id)
            if where is not None:
                ids = ids.where(where)

            def run():
                with session.no_autoflush:
                    session.execute(delete(self.model).where(self.record_column.in_(ids)))
            self._guarded(session, run)
            return
        query = select(AppDataRecord.id)
        if where is not None:
            query = query.where(where)
        with session.no_autoflush:
            session.info.setdefault(self._bulk_refresh, set()).update(session.execute(query).scalars())

    def _before_commit(self, session: Session) -> None:
        if session.info.get(self._busy):
            return
        ids: set[str] = session.info.pop(self._bulk_refresh, set())
        companies: set[str] = session.info.pop(self._bulk_companies, set())
        if not ids and not companies:
            return
        session.flush()
        if ids:
            rows = [tuple(r) for r in session.execute(select(*_SOURCE).where(AppDataRecord.id.in_(ids))).all()]
            self.drop(session, ids - {r[0] for r in rows})
            self.replace(session, rows)
        for company_id in companies:  # bulk-inserted records: fill those without rows yet
            missing = session.execute(
                select(*_SOURCE).where(
                    AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(self.collections),
                    ~exists().where(and_(self.record_column == AppDataRecord.id)))).all()
            self.insert_only(session, [tuple(r) for r in missing])

    def _forget(self, session: Session) -> None:
        for key in (self._refresh, self._drop, self._bulk_refresh, self._bulk_companies):
            session.info.pop(key, None)
