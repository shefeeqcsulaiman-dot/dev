"""Employee requests as real rows (the request_records table): the main copy of each request.

Overtime, loans, salary advances, attendance corrections and task requests each get one
row here: employee, department, status, whether it's still open (app/workflow_status.py),
request date, amount, hours, months, reason and title as columns, and every other field in
`extra`, so the row holds the whole request. Every write, from any path (HRMS, ESS), is put
through its row (canonical_request(), applied in models.py). Unlike the other main-copy
tables, nothing in a request's JSON is normalised: payroll and the screens read these
fields as saved (overtime hours are saved as text), so the JSON stays exactly as it is and
only the columns hold parsed values. Check, rebuild or rewrite with
`python -m app.request_records [--rebuild] [--rewrite-json]`.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import main_copy
from app.doc_index import parse_document_date
from app.main_copy import Field, fields_from, record_from
from app.models import AppDataRecord, RequestRecord
from app.parties import _text
from app.record_mirror import Record, RecordMirror
from app.workflow_status import is_finished_status

COLLECTIONS = ("overtimeRequests", "employeeLoans", "salaryAdvances", "attendanceCorrections", "taskRequests")
FIELDS = (Field("employee_id", 80, column="employee_no"), Field("employee", 160, column="employee_name"),
          Field("department", 120), Field("status", 40), Field("date", 40, column="request_date"),
          Field("amount", kind="number"), Field("ot_hours", kind="number", column="hours"),
          Field("months", kind="number"), Field("reason", 1000), Field("title", 255))
_NUMBERS = ("amount", "hours", "months")
_COLUMNS = ("employee_no", "employee_name", "department", "status", "request_date", "amount", "hours", "months",
            "reason", "title")
_COMPARED = ("collection", "record_key", *_COLUMNS, "is_open", "extra")
_FOUR = Decimal("0.0001")


def request_fields(doc: dict[str, Any]) -> dict[str, Any]:
    return fields_from(doc, FIELDS)


def canonical_request(record: dict[str, Any]) -> dict[str, Any]:
    """A request as it is stored: put through its row and back (the same JSON)."""
    return record_from(request_fields(record), FIELDS)


def _column(col: str, value: Any) -> Any:
    if col == "request_date":
        parsed = parse_document_date(value) if value not in (None, "") else None
        return parsed.isoformat() if parsed else None
    if col in _NUMBERS:
        return Decimal(str(value)).quantize(_FOUR) if value is not None and abs(value) < 1e12 else None
    return value or None


def request_rows(rec: Record) -> list[dict[str, Any]]:
    record_id, company_id, collection, payload, branch_id, record_key = rec
    try:
        doc = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(doc, dict):
        return []
    fields = request_fields(doc)
    return [{
        "id": record_id,
        "company_id": company_id,
        "branch_id": branch_id,
        "collection": collection,
        "record_key": _text(record_key, 160),
        **{col: _column(col, fields.get(col)) for col in _COLUMNS},
        "is_open": not is_finished_status(doc.get("status")),
        "extra": fields["extra"],
    }]


mirror = RecordMirror("request_records", COLLECTIONS, RequestRecord, RequestRecord.id, request_rows).register()


def verify_company(session: Session, company_id: str) -> bool:
    """True when the company's request_records rows match its requests."""
    have = {r.id: tuple(getattr(r, c) for c in _COMPARED)
            for r in session.query(RequestRecord).filter(RequestRecord.company_id == company_id)}
    want = {}
    for rec in session.execute(select(AppDataRecord.id, AppDataRecord.company_id, AppDataRecord.collection,
                                      AppDataRecord.payload, AppDataRecord.branch_id, AppDataRecord.record_key)
                               .where(AppDataRecord.company_id == company_id, AppDataRecord.collection.in_(COLLECTIONS))):
        for row in request_rows(tuple(rec)):
            want[row["id"]] = tuple(row[c] for c in _COMPARED)
    return have == want


def stale_json(session: Session, company_id: str) -> list[str]:
    return main_copy.stale_json(session, company_id, COLLECTIONS, canonical_request)


def rewrite_json(session: Session, record_ids: list[str]) -> None:
    main_copy.rewrite_json(session, record_ids, canonical_request)


def _main() -> None:
    main_copy.check_tool("Check (or rebuild) the request_records table.", COLLECTIONS, verify_company,
                         canonical_request, mirror)


if __name__ == "__main__":
    _main()
