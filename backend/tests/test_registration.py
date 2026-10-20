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


def test_self_service_trial_signs_in_and_is_listed_for_follow_up(client, db):
    """The sign-up page creates a 3-day trial (signup.html -> POST /auth/register): the
    returned token works, the email and password sign in, the company expires in 3 days,
    and the sign-up shows in Super Admin > Trial Requests (converted)."""
    import uuid
    from datetime import date, timedelta

    from app.models import Company, TrialRequest, User

    email = f"trial-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Trial-pass-1", "full_name": "Trial Owner", "company_name": "Trial Co",
        "country": "United Arab Emirates", "employee_count": "1–10", "notes": "We run two shops"})
    assert r.status_code == 201, r.text
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200, me.text
    assert client.post("/api/v1/auth/login", json={"email": email, "password": "Trial-pass-1"}).status_code == 200

    user = db.query(User).filter(User.email == email).one()
    company = db.get(Company, user.company_id)
    assert company.subscription_expires_at == (date.today() + timedelta(days=3)).isoformat()
    lead = db.query(TrialRequest).filter(TrialRequest.email == email).one()
    assert lead.status == "converted" and "3 days" in lead.notes and "We run two shops" in lead.notes

    # The same email can't start a second trial.
    again = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Trial-pass-2", "full_name": "Another Owner", "company_name": "Another Co"})
    assert again.status_code == 409


def test_register_rejects_invalid_signup_fields(client, db):
    """Sign-up validation (RegisterRequest): bad names, weak passwords, bad phone numbers,
    links in the company name and unknown employee ranges are refused with a clear reason,
    and no company is created."""
    from app.models import Company

    base = {"email": "valid-signup@example.com", "password": "Trial-pass-1", "full_name": "Sara Ahmed",
            "company_name": "Valid Signup Co", "phone": "+971 50 123 4567", "employee_count": "5–9"}
    cases = [
        ({"full_name": "X"}, "full name"),
        ({"full_name": "John 123"}, "full name"),
        ({"company_name": "Visit http://spam.example"}, "company name"),
        ({"password": "abcdefgh"}, "letter and one number"),
        ({"password": "12345678"}, "letter and one number"),
        ({"password": "short1"}, "8 to 128"),
        ({"phone": "call me"}, "phone"),
        ({"phone": "+971 12"}, "phone"),
        ({"employee_count": "1000"}, "employees"),
        ({"email": "not-an-email"}, "email"),
        ({"email": "same1pass@example.com", "password": "same1pass@example.com"}, "same as your email"),
    ]
    for change, reason in cases:
        r = client.post("/api/v1/auth/register", json={**base, **change})
        assert r.status_code == 422, (change, r.text)
        assert reason in r.text, (change, r.text)
    assert db.query(Company).filter(Company.name == "Valid Signup Co").count() == 0

    ok = client.post("/api/v1/auth/register", json={**base, "full_name": "  Sara   Ahmed "})
    assert ok.status_code == 201, ok.text
    db.expire_all()
    assert db.query(Company).filter(Company.name == "Valid Signup Co").count() == 1
