"""Deleting ledger rows that were bank-reconciled: the match is released (statement line
back to unmatched) instead of PostgreSQL rejecting the delete on the foreign key."""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models import BankReconciliationMatch, BankStatementLine
from tests.conftest import ensure_user


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"rc-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"96{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user.company_id


def _reconcile_gl_row(client, headers, voucher_no, debit=None, credit=None):
    accounts = {row["code"]: row for row in client.get("/api/v1/accounts", headers=headers).json()}
    rows = client.get("/api/v1/general-ledger", headers=headers).json()
    row = next(r for r in rows if r["voucher_no"] == voucher_no
               and (debit is None or Decimal(r["debit"]) == debit) and (credit is None or Decimal(r["credit"]) == credit))
    bank = client.post("/api/v1/bank-accounts", headers=headers,
                       json={"account_id": accounts["1000"]["id"], "bank_name": "QA Bank", "account_number": uuid4().hex[:8]})
    assert bank.status_code == 201, bank.text
    line = client.post("/api/v1/bank-statement-lines", headers=headers, json={
        "bank_account_id": bank.json()["id"], "statement_date": "2026-09-30", "transaction_date": "2026-09-15",
        "reference_no": voucher_no, "credit": "0", "debit": "1.00"})
    assert line.status_code == 201, line.text
    matched = client.post("/api/v1/bank-reconciliation/matches", headers=headers,
                          json={"statement_line_id": line.json()["id"], "ledger_entry_id": row["id"]})
    assert matched.status_code == 201, matched.text
    return line.json()["id"]


def _released(db, statement_line_id):
    db.expire_all()
    assert db.get(BankStatementLine, statement_line_id).status == "unmatched"
    assert db.query(BankReconciliationMatch).filter(BankReconciliationMatch.statement_line_id == statement_line_id).count() == 0


def test_clear_all_releases_reconciled_rows(client, db, tenant):
    headers, _cid = tenant
    accounts = {row["code"]: row for row in client.get("/api/v1/accounts", headers=headers).json()}
    entry_no = f"RC-{uuid4().hex[:6]}"
    r = client.post("/api/v1/journal", headers=headers, json={"entry_number": entry_no, "description": "reconciled", "lines": [
        {"account_id": accounts["1000"]["id"], "debit": 40, "credit": 0},
        {"account_id": accounts["3000"]["id"], "debit": 0, "credit": 40}]})
    assert r.status_code == 201, r.text
    statement_line = _reconcile_gl_row(client, headers, entry_no, debit=Decimal("40.00"))
    cleared = client.post("/api/v1/journal/clear-all", headers=headers)
    assert cleared.status_code == 200, cleared.text
    _released(db, statement_line)


def test_deleting_a_sales_invoice_releases_its_reconciled_row(client, db, tenant):
    headers, _cid = tenant
    no = f"RC-INV-{uuid4().hex[:6]}"
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "salesInvoices", "record": {
        "invoice_no": no, "customer": "Recon Co", "date": "2026-09-10", "status": "Issued",
        "lines": [{"description": "x", "qty": 1, "unit_price": 100, "vat_rate": 5}]}})
    assert r.status_code == 200, r.text
    rows = client.get("/api/v1/general-ledger", headers=headers).json()
    voucher = next(r["voucher_no"] for r in rows if no in (r.get("voucher_no") or "") or no in (r.get("narration") or ""))
    statement_line = _reconcile_gl_row(client, headers, voucher)
    deleted = client.post("/api/v1/app-data?action=delete", headers=headers, json={"collection": "salesInvoices", "record": {"invoice_no": no}})
    assert deleted.status_code == 200, deleted.text
    _released(db, statement_line)
