"""Tasks as real rows (the task_records table): the main copy of each task.

Every task has one row here with its fields as columns (title, department, assignee, status,
priority, progress, due date, repeat) and its other fields (description, colour, checklist,
dates...) in `extra`, so the row holds the whole task; is_open is derived from the status
(app/workflow_status.py, the same rule the bootstrap uses). Every write of a task, from any
path (the HRMS screen, ESS, imports), is put through its row and the JSON regenerated from
it (canonical_task(), applied in models.py), kept because generic readers still read
app_data_records. Check, rebuild or rewrite with
`python -m app.task_records [--rebuild] [--rewrite-json]`.

Normalised (app/main_copy.py): progress is a whole number 0-100 (dropped when it isn't a
number), due_date is YYYY-MM-DD ("" when it isn't a date); text keeps its original value.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, TaskRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror
from app.workflow_status import is_finished_status

COLLECTIONS = ("tasks",)
FIELDS = (Field("title", 255), Field("department", 120), Field("assigned_to", 80), Field("assigned_to_name", 160),
          Field("status", 40), Field("priority", 20), Field("progress", kind="percent"), Field("due_date", kind="day"),
          Field("repeat", 20))


def task_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return fields_from(doc, FIELDS)


def canonical_task(record: dict[str, Any]) -> dict[str, Any]:
    """A task as it is stored: put through its row and back."""
    return record_from(task_fields(record), FIELDS)


def task_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, _collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = task_fields(doc)
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "record_key": _text(record_key, 160),
        **{key: (value or None) if key in _NULLABLE_EMPTY else value for key, value in fields.items()},
        "title": fields["title"] or "",
        "is_open": not is_finished_status(doc.get("status")),
    }]


_COMPARED = ("record_key", "title", "department", "assigned_to", "assigned_to_name", "status", "is_open",
             "priority", "progress", "due_date", "repeat", "extra")
# A text column left empty ("" in the record) is stored as NULL, as before step C.
_NULLABLE_EMPTY = frozenset(f.name for f in FIELDS if f.kind in ("text", "day")) - {"title"}


mirror = RecordMirror("task_records", COLLECTIONS, TaskRecord, TaskRecord.id, task_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's task_records rows match what its JSON tasks produce."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(TaskRecord).filter(TaskRecord.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id,
                                      AppDataRecord.collection.in_(COLLECTIONS))):
        for row in task_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def stale_json(session: Session, company_id: str) -> list[str]:
    return main_copy.stale_json(session, company_id, COLLECTIONS, canonical_task)


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    main_copy.rewrite_json(session, record_ids, canonical_task)


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the task_records table and the JSON it regenerates.", COLLECTIONS,
                         verify_company, canonical_task, mirror)


if __name__ == "__main__":
    _main()
