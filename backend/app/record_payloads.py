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

from sqlalchemy import event, select
from sqlalchemy.orm import Session, attributes

# Collection -> (typed model, function(row) -> JSON dict). Filled lazily (import cycles).
_REBUILDERS: dict[str, tuple[Any, Callable[[Any], dict]]] = {}
# Typed model -> function(session, records) -> {record id: row}, for a table not keyed by
# the record id (employees: by employee number). Others are found by id.
_LOADERS: dict[Any, Callable[[Session, list[Any]], dict[str, Any]]] = {}


def _rebuilders() -> dict[str, tuple[Any, Callable[[Any], dict]]]:
    if not _REBUILDERS:
        from app import lookup_records
        from app.main_copy import record_from
        from app.models import LookupRecord

        for collection in lookup_records.COLLECTIONS:
            _REBUILDERS[collection] = (LookupRecord, lambda row: record_from(row, lookup_records.FIELDS))
        from app import request_records
        from app.models import RequestRecord

        for collection in request_records.COLLECTIONS:
            _REBUILDERS[collection] = (RequestRecord, lambda row: record_from(row, request_records.FIELDS))
        from app import task_records
        from app.models import TaskRecord

        _REBUILDERS["tasks"] = (TaskRecord, lambda row: record_from(row, task_records.FIELDS))
        from app import rota_records
        from app.models import RotaRecord

        _REBUILDERS["rotaAssignments"] = (RotaRecord, lambda row: record_from(row, rota_records.FIELDS))
        from app import parties
        from app.models import Party

        for collection in parties.KINDS:
            _REBUILDERS[collection] = (Party, parties.party_record)
        from app import employee_records
        from app.models import Employee

        _REBUILDERS["employees"] = (Employee, employee_records.employee_record)
        _LOADERS[Employee] = employee_records.typed_rows
        from app import catalog_products
        from app.models import CatalogProduct

        _REBUILDERS["products"] = (CatalogProduct, catalog_products.rebuilt_product)
        _LOADERS[CatalogProduct] = _products_with_images
        from app import document_records
        from app.models import DocumentRecord

        for collection in document_records.COLLECTIONS:
            _REBUILDERS[collection] = (DocumentRecord, document_records.rebuilt_document)
        _LOADERS[DocumentRecord] = document_records.typed_documents
        from app import purchase_documents
        from app.models import PurchaseDocumentRecord

        _REBUILDERS["purchaseDocuments"] = (PurchaseDocumentRecord, purchase_documents.rebuilt_document)
    return _REBUILDERS


def _products_with_images(session: Session, objs: list[Any]) -> dict[str, Any]:
    """catalog_products rows by record id, image included (a deferred column on the model)."""
    from app.models import CatalogProduct

    table = CatalogProduct.__table__
    typed = {}
    for match in id_batches(session, table.c.id, [o.id for o in objs]):
        for row in session.execute(select(table).where(match)):
            typed[row.id] = row
    return typed


def id_batches(session: Session, column: Any, ids: list[str]):
    """(column matches these ids) filters, in batches: on PostgreSQL one array parameter
    per 5,000 ids (= ANY), elsewhere IN lists of 500 -- building and parsing hundreds of
    500-value IN lists cost seconds on a 50,000-record read."""
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import String, any_, bindparam
        from sqlalchemy.dialects.postgresql import ARRAY

        for start in range(0, len(ids), 5000):
            yield column == any_(bindparam("ids", ids[start:start + 5000], type_=ARRAY(String)))
    else:
        for start in range(0, len(ids), 500):
            yield column.in_(ids[start:start + 500])


def _typed_rows(session: Session, objs: list[Any]) -> dict[str, Any]:
    """{record id: its typed row} for these records (all in STORED_IN_TABLE). Rows are read
    as plain column rows (not ORM objects): several times cheaper for a big page, and always
    what the database holds now (the mirrors write the tables without the ORM)."""
    by_model: dict[Any, list[Any]] = {}
    for obj in objs:
        by_model.setdefault(_rebuilders()[obj.collection][0], []).append(obj)
    typed: dict[str, Any] = {}
    for model, group in by_model.items():
        loader = _LOADERS.get(model)
        if loader is not None:
            typed.update(loader(session, group))
            continue
        table = model.__table__
        for match in id_batches(session, table.c.id, [o.id for o in group]):
            for row in session.execute(select(table).where(match)):
                typed[row.id] = row
    return typed


# Collections whose JSON is not stored. Grows one type at a time.
STORED_IN_TABLE = frozenset({
    # lookup lists (2026-10-13, migration 0034)
    "salesUnits", "salesCategories", "serviceTypes", "salesPeople", "rotaShifts", "hrHolidays", "bankAccounts",
    # employee requests and HR workflow lists (2026-10-13, migration 0036)
    "overtimeRequests", "employeeLoans", "salaryAdvances", "attendanceCorrections", "taskRequests",
    "payrollAdjustments", "rotaSwaps", "rotaApprovals", "jobRequisitions", "candidates",
    # tasks (2026-10-13, migration 0037)
    "tasks",
    # rota assignments (2026-10-13, migration 0038)
    "rotaAssignments",
    # customers and suppliers (2026-10-13, migration 0039)
    "customers", "vendors",
    # employees (2026-10-13, migration 0040)
    "employees",
    # products (2026-10-13, migration 0041)
    "products",
    # sales invoices, purchases, bills, quotations, expenses, payments (2026-10-13, migration 0042)
    "salesInvoices", "purchaseRecords", "bills", "quotations", "expenses", "payments",
    # uploaded purchase files and POS receipts (2026-10-14, migration 0043)
    "purchaseDocuments", "posSales",
})


def rebuild(collection: str, typed_row: Any) -> dict[str, Any]:
    model, fn = _rebuilders()[collection]
    return fn(typed_row)


def fill_session(session: Session | None) -> None:
    """Fill in the JSON of every loaded record in STORED_IN_TABLE whose payload is empty."""
    if session is None:
        return
    from app.models import AppDataRecord

    pending = [obj for obj in list(session.identity_map.values())
               if isinstance(obj, AppDataRecord) and obj.collection in STORED_IN_TABLE and not obj._payload]
    if not pending:
        return
    with session.no_autoflush:
        typed = _typed_rows(session, pending)
        for obj in pending:
            row = typed.get(obj.id)
            text = json.dumps(rebuild(obj.collection, row), ensure_ascii=False, default=str) if row else "{}"
            attributes.set_committed_value(obj, "_payload", text)


RECORD_COLUMNS = ("id", "company_id", "collection", "record_key", "_payload")


def record_dicts(session: Session, rows: list[Any], headers_only: bool = False) -> list[dict[str, Any]]:
    """Each record's JSON as a dict, for readers of many records that only need their content
    (reports): rows are column rows with RECORD_COLUMNS (select them with
    record_columns()); no ORM objects and no JSON text round trip for rebuilt ones. A record
    whose JSON can't be read gives {}. headers_only: documents rebuilt without their
    "lines" (no lines read) -- for readers that use only header fields."""
    pending = [row for row in rows if not row._payload and row.collection in STORED_IN_TABLE]
    with session.no_autoflush:
        if headers_only:
            from app import document_records

            docs = [row for row in pending if row.collection in document_records.FIELDS]
            others = [row for row in pending if row.collection not in document_records.FIELDS]
            typed = {**(_typed_rows(session, others) if others else {}),
                     **(document_records.typed_documents(session, docs, with_lines=False) if docs else {})}
        else:
            typed = _typed_rows(session, pending) if pending else {}
    out = []
    for row in rows:
        if row._payload:
            try:
                doc = json.loads(row._payload)
            except (TypeError, ValueError):
                doc = {}
        else:
            found = typed.get(row.id)
            doc = rebuild(row.collection, found) if found is not None else {}
        out.append(doc if isinstance(doc, dict) else {})
    return out


def record_columns() -> tuple[Any, ...]:
    from app.models import AppDataRecord

    return (AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection, AppDataRecord.record_key,
            AppDataRecord._payload.label("_payload"))


def previous_json(session: Session, obj: Any) -> str:
    """The JSON of a record as its typed row in the database has it -- in after_flush, before
    the mirrors rewrite the row: the version this flush replaces or deletes ("" if none)."""
    with session.no_autoflush:
        row = _typed_rows(session, [obj]).get(obj.id)
    return json.dumps(rebuild(obj.collection, row), ensure_ascii=False, default=str) if row is not None else ""


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
    blank_if_rebuilt(session, list(session.info.pop("_payload_written", set())))


def blank_if_rebuilt(session: Session, objs: list[Any]) -> None:
    """Empty the stored JSON of these records (in STORED_IN_TABLE, as written to the database)
    whose typed row rebuilds exactly the same JSON; any other keeps its stored JSON, so
    nothing can be lost. Also called directly where the typed row is completed after the
    record's own write (employees, app/employee_records.py)."""
    from app.models import AppDataRecord

    written = [obj for obj in objs if obj.collection in STORED_IN_TABLE and obj._payload]
    if not written:
        return
    targets = []
    with session.no_autoflush:
        typed = _typed_rows(session, written)
        for obj in written:
            try:
                doc = json.loads(obj._payload)
            except (TypeError, ValueError):
                continue
            row = typed.get(obj.id)
            if row is not None and isinstance(doc, dict) and rebuild(obj.collection, row) == doc:
                targets.append(obj)
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
