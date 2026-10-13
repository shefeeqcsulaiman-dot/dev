"""request_records is the main copy of employee requests (step C): the row holds the whole
request, the JSON stays exactly as saved (payroll and the screens read it as is), and the
columns hold parsed values."""
import json
import uuid
from decimal import Decimal

from app import request_records
from app.models import AppDataRecord, RequestRecord
from app.request_records import canonical_request
from tests.test_hrms_department_scope import _company_id, _save


def test_json_is_left_exactly_as_saved():
    docs = [
        {"id": "OT-1", "employee": " Ali ", "employee_id": "E1", "date": "2026-10-13", "ot_hours": "2.5", "ot_type": "normal",
         "multiplier": 1.25, "status": "Pending", "reason": "", "submitted": "2026-10-13T09:00:00Z", "source": "ess"},
        {"id": "LN-1", "employee_id": "E1", "type": "Personal Loan", "amount": 5000, "emi": 416.67, "months": 12,
         "balance": 5000.0, "deduct_from": "", "status": "Approved"},
        {"id": "ADV-1", "employee_id": "E1", "amount": "1,000", "month": "2026-11", "status": True, "td": None},
    ]
    for doc in docs:
        out = canonical_request(doc)
        assert out == doc and [type(v) for v in out.values()] == [type(doc[k]) for k in out], doc["id"]


def test_columns_hold_parsed_values_for_every_writer(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    tag = uuid.uuid4().hex[:6]
    ot = {"id": f"OT-{tag}", "employee": "Ali", "employee_id": f"E-{tag}", "department": "Sales", "date": "13/10/2026",
          "ot_hours": "2.5", "ot_type": "weekend", "status": "Pending", "reason": "stock count"}
    assert _save(client, auth_headers, "overtimeRequests", ot).status_code == 200
    loan = {"id": f"LN-{tag}", "employee_id": f"E-{tag}", "type": "Personal Loan", "amount": "5,000", "months": 12,
            "status": "Closed"}
    assert _save(client, auth_headers, "employeeLoans", loan).status_code == 200
    db.expire_all()
    rows = {r.record_key: r for r in db.query(RequestRecord).filter(RequestRecord.company_id == cid,
                                                                     RequestRecord.record_key.like(f"%-{tag}"))}
    o, ln = rows[f"OT-{tag}"], rows[f"LN-{tag}"]
    assert (o.collection, o.employee_no, o.request_date, o.hours, o.is_open, o.reason) == \
           ("overtimeRequests", f"E-{tag}", "2026-10-13", Decimal("2.5"), True, "stock count")
    assert (ln.amount, ln.months, ln.is_open) == (Decimal("5000"), Decimal("12"), False)
    stored = json.loads(db.query(AppDataRecord.payload).filter(AppDataRecord.record_key == f"OT-{tag}").scalar())
    assert stored["ot_hours"] == "2.5" and stored["date"] == "13/10/2026"    # unchanged
    assert json.loads(o.extra)["ot_type"] == "weekend"
    assert request_records.verify_company(db, cid) and request_records.stale_json(db, cid) == []
