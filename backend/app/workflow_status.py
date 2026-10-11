"""When a workflow item (a task, a request, a loan) counts as finished.

One rule, shared by the bootstrap's open-work window (routers/app_data.py) and the typed
task_records table's is_open column (app/task_records.py), so both always agree."""
from typing import Any

FINISHED_STATUS_WORDS = ("done", "complete", "closed", "reject", "cancel", "repaid", "paid", "declined",
                         "archived", "hired", "withdrawn")


def is_finished_status(status: Any) -> bool:
    text = str(status or "").strip().lower()
    return bool(text) and any(word in text for word in FINISHED_STATUS_WORDS)
