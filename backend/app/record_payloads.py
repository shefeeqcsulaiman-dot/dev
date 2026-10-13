"""JSON no longer stored for collections whose typed table is the main copy (step D).

For a collection in STORED_IN_TABLE, app_data_records.payload is left empty after each
write and the record is rebuilt from its typed table when read. AppDataRecord.payload is a
property (models.py): the first time code reads an empty payload of such a record, every
such record loaded in the session gets its JSON filled in at once -- one query per table,
not one per record -- marked as already saved, so it is never written back. Code that
loads whole records (bootstrap, lists, exports, the SQL backup, edits) works unchanged;
code that selects only the payload column has to load the records instead.

Writes keep sending the full JSON: the typed row is built from it as before (the mirrors
in app/record_mirror.py run first), then blank_stored_payloads() empties the column.
A collection is only added here once its typed table rebuilds the JSON losslessly
(tests/test_record_payloads.py) and every column-level reader of it is converted.
"""
from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session, attributes

# Collection -> (typed model, function(row) -> JSON dict). Filled lazily (import cycles).
_REBUILDERS: dict[str, tuple[Any, Callable[[Any], dict]]] = {}


def _rebuilders() -> dict[str, tuple[Any, Callable[[Any], dict]]]:
    if not _REBUILDERS:
        from app import lookup_records
        from app.main_copy import record_from
        from app.models import LookupRecord

        for collection in lookup_records.COLLECTIONS:
            _REBUILDERS[collection] = (LookupRecord, lambda row: record_from(row, lookup_records.FIELDS))
    return _REBUILDERS


# Collections whose JSON is not stored. Grows one type at a time.
STORED_IN_TABLE = frozenset({
    "salesUnits", "salesCategories", "serviceTypes", "salesPeople", "rotaShifts", "hrHolidays", "bankAccounts",
})


def rebuild(collection: str, typed_row: Any) -> dict[str, Any]:
    model, fn = _rebuilders()[collection]
    return fn(typed_row)


def fill_session(session: Session | None) -> None:
    """Fill in the JSON of every loaded record in STORED_IN_TABLE whose payload is empty."""
    if session is None:
        return
    from app.models import AppDataRecord

    pending: dict[str, list[Any]] = {}
    for obj in list(session.identity_map.values()):
        if isinstance(obj, AppDataRecord) and obj.collection in STORED_IN_TABLE and not obj._payload:
            pending.setdefault(obj.collection, []).append(obj)
    by_model: dict[Any, list[Any]] = {}
    for collection, objs in pending.items():
        by_model.setdefault(_rebuilders()[collection][0], []).extend(objs)
    with session.no_autoflush:
        for model, objs in by_model.items():
            ids = [o.id for o in objs]
            typed = {}
            for start in range(0, len(ids), 500):
                for row in session.query(model).filter(model.id.in_(ids[start:start + 500])):
                    typed[row.id] = row
            for obj in objs:
                row = typed.get(obj.id)
                text = json.dumps(rebuild(obj.collection, row), ensure_ascii=False, default=str) if row else "{}"
                attributes.set_committed_value(obj, "_payload", text)


def payload_texts(session: Session, rows: list[Any]) -> list[str]:
    """JSON text of these records (loaded AppDataRecord objects), rebuilt where not stored."""
    return [row.payload for row in rows]


@event.listens_for(Session, "after_flush")
def _note_written_payloads(session: Session, _ctx: Any) -> None:
    from app.models import AppDataRecord

    written = session.info.setdefault("_payload_written", set())
    for obj in list(session.new) + list(session.dirty):
        if (isinstance(obj, AppDataRecord) and obj.collection in STORED_IN_TABLE
                and attributes.get_history(obj, "_payload").has_changes()):
            written.add(obj)


@event.listens_for(Session, "after_flush_postexec")
def blank_stored_payloads(session: Session, _ctx: Any) -> None:
    """After the typed rows are written (the mirrors run first on this same event), empty
    the stored JSON of records in STORED_IN_TABLE written in this flush; it is rebuilt from
    the table when read. A Core UPDATE on the table: an ORM-level one would make the mirrors
    rebuild the typed rows from the now-empty JSON."""
    from app.models import AppDataRecord

    targets = [obj for obj in session.info.pop("_payload_written", set()) if obj._payload]
    if not targets:
        return
    table = AppDataRecord.__table__
    ids = [obj.id for obj in targets]
    for start in range(0, len(ids), 500):
        session.execute(table.update().where(table.c.id.in_(ids[start:start + 500])).values(payload=""))
    for obj in targets:
        attributes.set_committed_value(obj, "_payload", "")


@event.listens_for(Session, "after_rollback")
def _forget_written(session: Session) -> None:
    session.info.pop("_payload_written", None)
