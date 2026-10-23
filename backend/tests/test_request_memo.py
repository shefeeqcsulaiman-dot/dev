"""app/request_memo.py (2026-10-23): company settings are read once per request, and any write
in that request empties the memory, so a value read before a save is never used after it."""
import json
import uuid

from app.models import AppDataRecord
from app.request_memo import memo
from app.routers.attendance import _weekend_day_set
from app.routers.leave import _leave_type_caps
from tests.conftest import ensure_user


def test_memo_reads_once_and_forgets_after_a_write(db):
    calls = []
    def compute():
        calls.append(1)
        return {"n": len(calls)}
    assert memo(db, ("t", "x"), compute) == {"n": 1}
    got = memo(db, ("t", "x"), compute)
    assert got == {"n": 1} and len(calls) == 1          # read once
    got["n"] = 99                                        # a caller changing its copy
    assert memo(db, ("t", "x"), compute) == {"n": 1}     # doesn't change the next caller's
    user = ensure_user(db, f"memo-{uuid.uuid4().hex[:6]}@taxflowqa.com", "900000000000991")
    db.add(AppDataRecord(company_id=user.company_id, collection="memo_test", record_key=uuid.uuid4().hex, payload="{}"))
    db.flush()                                           # a write in the same request...
    assert memo(db, ("t", "x"), compute) == {"n": 2}     # ...and the value is read again
    db.rollback()


def test_settings_saved_mid_request_are_seen(db):
    user = ensure_user(db, f"memo2-{uuid.uuid4().hex[:6]}@taxflowqa.com", "900000000000992")
    cid = user.company_id
    db.commit()
    assert _weekend_day_set(db, cid) == {0, 6}
    db.add(AppDataRecord(company_id=cid, collection="hr_settings", record_key="weekend-policy-config",
                         payload=json.dumps({"id": "weekend-policy-config", "mode": "fri_sat"})))
    db.flush()
    assert _weekend_day_set(db, cid) == {5, 6}
    caps = _leave_type_caps(db, cid)
    db.add(AppDataRecord(company_id=cid, collection="hrLeavePolicy", record_key="leave-policy",
                         payload=json.dumps({"id": "leave-policy", "leave_types": [{"type": "Sick Leave", "days": 15}]})))
    db.flush()
    assert caps.get("Sick Leave") != 15 and _leave_type_caps(db, cid)["Sick Leave"] == 15
    db.rollback()
