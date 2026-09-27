"""Regression coverage for POST /auth/register seeding a working chart of
accounts. seed_accounts() previously had zero callers anywhere in the
codebase — every company that ever registered started with an empty
accounts table. post_source_transaction() requires control accounts
1100 (Accounts Receivable) and 2200 (VAT Output Payable) to post anything;
without them the posting job fails silently (the invoice/purchase save
still returns 200, "Invoice saved" shows in the UI) and nothing ever
reaches the General Ledger or VAT report. Found via an E2E Playwright test
that created a real invoice and traced the missing journal entry back to
this exact failure mode."""
from app.models import Account, JournalEntry, PostingJob, SourceTransaction


def _register(client, suffix):
    r = client.post("/api/v1/auth/register", json={
        "email": f"newco-{suffix}@example.com",
        "password": "newco12345",
        "full_name": "New Co Owner",
        "company_name": f"New Co {suffix}",
    })
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_registration_seeds_control_accounts(client, db):
    headers = _register(client, "accounts-1")
    r = client.get("/api/v1/accounts", headers=headers)
    assert r.status_code == 200
    codes = {a["code"] for a in r.json()}
    # The two control accounts post_source_transaction() hard-requires.
    assert {"1100", "2200"}.issubset(codes), f"missing control accounts, got {codes}"


def test_freshly_registered_company_can_post_a_sales_invoice(client, db):
    headers = _register(client, "posting-1")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    company_id = me["company"]["id"]

    payload = {
        "collection": "salesInvoices",
        "record": {
            "invoice_no": "REG-TEST-001",
            "customer": "Regression Test Customer",
            "date": "2026-07-29",
            "subtotal": 1000,
            "vat_amount": 50,
            "total": 1050,
            # Not "Draft" — draft invoices are deliberately not posted to
            # the ledger (see sync_sales_invoice() in app_data.py); this
            # test is about seeded control accounts letting a REAL posting
            # succeed, so it needs a status that actually posts.
            "status": "Issued",
            "lines": [{"description": "Test line", "qty": 1, "unit_price": 1000, "vat_rate": 5}],
        },
    }
    r = client.post("/api/v1/app-data", params={"action": "save"}, headers=headers, json=payload)
    assert r.status_code == 200, r.text

    source = (
        db.query(SourceTransaction)
        .filter(SourceTransaction.company_id == company_id, SourceTransaction.reference == "REG-TEST-001")
        .one()
    )
    job = db.query(PostingJob).filter(PostingJob.source_id == source.id).one()
    assert job.status == "posted", f"posting job failed: {job.error_message}"

    journal = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_id == source.id)
        .one()
    )
    assert journal.status == "posted"
    total_debit = sum(line.debit for line in journal.lines)
    total_credit = sum(line.credit for line in journal.lines)
    assert total_debit == total_credit  # double-entry must balance
