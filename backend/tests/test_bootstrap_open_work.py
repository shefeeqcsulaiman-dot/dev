"""The bootstrap's newest-N cap cuts old finished history only: open work (a task not
done, an active loan, a pending request) older than the window is still sent, instead of
silently disappearing from the screens once enough newer items exist."""
import datetime as dt
import json
from uuid import uuid4

from app.models import AppDataRecord
from tests.conftest import ensure_user


def _tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"ow-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"93{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _add(db, company_id, collection, key, status, minutes):
    db.add(AppDataRecord(company_id=company_id, collection=collection, record_key=key,
                         payload=json.dumps({"id": key, "title": key, "status": status}),
                         created_at=dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=minutes)))


def test_old_open_items_survive_the_cap(client, db):
    company_id, headers = _tenant(client, db)
    # The 5 oldest tasks: 3 still open, 2 done. Then 500 newer tasks (all done).
    for i, status in enumerate(["To Do", "Done", "In Progress", "Completed", "Blocked"]):
        _add(db, company_id, "tasks", f"OLD-{i}", status, i)
    for i in range(500):
        _add(db, company_id, "tasks", f"NEW-{i}", "Done", 100 + i)
    # A loan from long ago still being repaid, plus a closed one, behind 500 newer loans.
    _add(db, company_id, "employeeLoans", "LOAN-ACTIVE", "Approved", 0)
    _add(db, company_id, "employeeLoans", "LOAN-CLOSED", "Closed", 1)
    for i in range(500):
        _add(db, company_id, "employeeLoans", f"LOAN-{i}", "Closed", 100 + i)
    db.commit()

    body = client.get("/api/v1/app-data", headers=headers).json()
    data = body.get("data", body)
    tasks = [t["id"] for t in data["tasks"]]
    assert [t for t in tasks if t.startswith("OLD-")] == ["OLD-0", "OLD-2", "OLD-4"]   # open ones, oldest first
    assert len(tasks) == 503 and tasks[3] == "NEW-0" and tasks[-1] == "NEW-499"
    loans = [l["id"] for l in data["employeeLoans"]]
    assert "LOAN-ACTIVE" in loans and "LOAN-CLOSED" not in loans
    assert "tasks" in body.get("truncated_collections", [])
