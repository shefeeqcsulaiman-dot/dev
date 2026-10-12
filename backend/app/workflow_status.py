"""When a workflow item (a task, a request, a loan) counts as finished.

One rule, shared by the bootstrap's open-work window (routers/app_data.py) and the typed
task_records table's is_open column (app/task_records.py), so both always agree. For the
other open-work collections the status is stamped into app_data_records.doc_status on
every save (models.py), and finished_status_sql() is the same rule in SQL."""
import json
from typing import Any

# Workflow collections whose still-open items the bootstrap always carries (app_data.py).
OPEN_WORK_COLLECTIONS = frozenset({
    "tasks", "overtimeRequests", "leaveRequests", "attendanceCorrections", "employeeLoans",
    "salaryAdvances", "jobRequisitions", "candidates", "rotaSwaps", "rotaApprovals", "taskRequests",
})

FINISHED_STATUS_WORDS = ("done", "complete", "closed", "reject", "cancel", "repaid", "paid", "declined",
                         "archived", "hired", "withdrawn")


def is_finished_status(status: Any) -> bool:
    text = str(status or "").strip().lower()
    return bool(text) and any(word in text for word in FINISHED_STATUS_WORDS)


def status_key(payload: str | None) -> str | None:
    """The record's status as stamped into doc_status: trimmed, lower case, 40 characters
    (the finished words all fit well inside), or None when it has none."""
    try:
        status = json.loads(payload or "{}").get("status")
    except (TypeError, ValueError, AttributeError):
        return None
    text = str(status or "").strip().lower()[:40]
    return text or None


def finished_status_sql(column: Any) -> Any:
    """is_finished_status() on a stamped status column, as an SQL condition."""
    from sqlalchemy import or_

    return or_(*[column.like(f"%{word}%") for word in FINISHED_STATUS_WORDS])
