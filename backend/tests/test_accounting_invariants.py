"""Accounting invariant test suite — the highest-priority layer of a
Google/Meta-style "Zero Functional Regression" QA program for TaxFlow.

Core principle: verify double-entry bookkeeping rules by querying the
DATABASE directly, never by trusting that an API call returned 200/201.
Research confirmed debit=credit balance is checked in THREE separate,
independent, unsynchronized places (accounting_posting.py::build_journal(),
accounting.py::create_journal(), accounting.py::validate_lines() used by
approve_voucher()/payments/receipts) — none DB-enforced, none protecting
any other insertion path. This suite is a permanent, code-path-agnostic
regression guard that doesn't care which of those three (or a future
fourth) app-level check ran.

Also covers two real bugs found and fixed alongside this suite (see the
plan file / commit message for the full writeup): manual journal creation
and journal reversal previously never set branch_id (always NULL, meaning
a reversal of branch-scoped data silently became visible company-wide),
and DELETE /journal/{id} previously had no guard against deleting an
already-posted journal (contradicting the documented "posted journals are
never deleted" invariant) — fixed with a single-entry guard plus a
separate, deliberate POST /journal/clear-all bulk-reset endpoint for the
"Clear Ledger Records" UI action, which intentionally bypasses that guard.
"""

from decimal import Decimal

from sqlalchemy import func

from app.models import Company, GeneralLedgerEntry, JournalEntry, JournalLine, SourceTransaction, User
from app.security import hash_password
from tests.conftest import seed_accounts


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _fresh_company_admin_headers(client, db, tag):
    """A brand-new, dedicated tenant — not the shared auth_headers/
    second_tenant_headers fixtures. Needed for any test whose side effects
    other test FILES make assumptions about (deleting/recreating a shared
    control account; creating a branch under a tenant another file expects
    to have zero branches) — auth_headers' tenant is shared for the whole
    pytest session across every test file, not just this one, so mutating
    its shared state is only safe for effects nothing else depends on."""
    company = Company(name=f"Invariant Test Co {tag}", trn=f"INVARIANT-TEST-{tag}", country="United Arab Emirates")
    db.add(company)
    db.flush()
    email = f"invariant-admin-{tag}@taxflowqa.com".lower()
    user = User(company_id=company.id, email=email, full_name="Invariant Test Admin", role="admin", password_hash=hash_password("admin123"))
    db.add(user)
    seed_accounts(db, company.id)
    db.commit()
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _accounts(client, headers):
    return {row["code"]: row for row in client.get("/api/v1/accounts", headers=headers).json()}


def _create_source_transaction(client, headers, module, reference, account_code, branch_id=None):
    payload = {
        "module": module,
        "reference": reference,
        "party_name": "Invariant Test Party",
        "lines": [{"description": "Invariant test line", "account_code": account_code, "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
    }
    if branch_id is not None:
        payload["branch_id"] = branch_id
    r = client.post("/api/v1/source-transactions", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _post_and_approve(client, headers, module, reference, account_code, branch_id=None):
    source = _create_source_transaction(client, headers, module, reference, account_code, branch_id)
    approved = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=headers)
    assert approved.status_code == 202, approved.text
    return source, approved.json()


def _journal_for_source(db, company_id, source_module, source_id):
    return (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == source_module, JournalEntry.source_id == source_id)
        .first()
    )


def _assert_journal_balanced(db, journal_id):
    total_debit, total_credit = (
        db.query(func.coalesce(func.sum(JournalLine.debit), 0), func.coalesce(func.sum(JournalLine.credit), 0))
        .filter(JournalLine.journal_id == journal_id)
        .first()
    )
    assert Decimal(total_debit) == Decimal(total_credit), f"journal {journal_id} unbalanced: debit={total_debit} credit={total_credit}"


def _assert_all_journals_balanced(db, company_id):
    rows = (
        db.query(JournalLine.journal_id, func.sum(JournalLine.debit), func.sum(JournalLine.credit))
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .filter(JournalEntry.company_id == company_id)
        .group_by(JournalLine.journal_id)
        .all()
    )
    assert rows, "expected at least one journal to sweep — test setup produced nothing to check"
    for journal_id, total_debit, total_credit in rows:
        assert Decimal(total_debit) == Decimal(total_credit), f"journal {journal_id} unbalanced: debit={total_debit} credit={total_credit}"


def test_failed_then_fixed_retry_produces_exactly_one_journal(client, db):
    """Uses a fresh, dedicated tenant (not the shared auth_headers) — it
    deletes and recreates a "2100" control account, and auth_headers' tenant
    is shared across the WHOLE pytest session (every test file, not just
    this one); test_accounting.py's own payment test also posts against
    2100, so relying on within-file ordering alone isn't enough to keep
    this delete safe once the full suite runs."""
    headers = _fresh_company_admin_headers(client, db, "RETRY")
    accounts = _accounts(client, headers)
    company_id = _company_id(client, headers)
    deleted = client.delete(f"/api/v1/accounts/{accounts['2100']['id']}", headers=headers)
    assert deleted.status_code == 204

    source, job = _post_and_approve(client, headers, "purchase", "SRC-INV-RETRY-001", "4000")
    assert job["status"] == "failed", job
    # The failed attempt must not have left a half-written journal — build_journal()
    # raises before db.add(journal), but this is exactly the kind of thing that
    # should be a permanent regression guard rather than inferred from reading
    # the code once.
    assert _journal_for_source(db, company_id, "purchase", source["id"]) is None

    recreated = client.post("/api/v1/accounts", headers=headers, json={"code": "2100", "name": "Accounts Payable", "type": "liability"})
    assert recreated.status_code == 201, recreated.text
    retried = client.post(f"/api/v1/posting-jobs/{job['id']}/retry", headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "posted"

    journal_count = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "purchase", JournalEntry.source_id == source["id"])
        .count()
    )
    assert journal_count == 1


# ── Category 1: DR=CR balance via direct DB query, every posting path ──────


def test_purchase_auto_post_produces_balanced_journal_in_db(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-PUR-001", "4000")
    assert job["status"] == "posted", job
    journal = _journal_for_source(db, company_id, "purchase", source["id"])
    assert journal is not None
    _assert_journal_balanced(db, journal.id)


def test_sales_auto_post_produces_balanced_journal_in_db(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "sales", "SRC-INV-SAL-001", "3000")
    assert job["status"] == "posted", job
    journal = _journal_for_source(db, company_id, "sales", source["id"])
    assert journal is not None
    _assert_journal_balanced(db, journal.id)


def test_manual_journal_post_is_balanced_in_db(client, db, auth_headers):
    accounts = _accounts(client, auth_headers)
    created = client.post(
        "/api/v1/journal", headers=auth_headers,
        json={
            "entry_number": "JRN-INV-001", "description": "Invariant balanced journal",
            "lines": [
                {"account_id": accounts["1100"]["id"], "debit": "105.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "100.00"},
                {"account_id": accounts["2200"]["id"], "debit": "0", "credit": "5.00"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    _assert_journal_balanced(db, created.json()["id"])


def test_voucher_approval_produces_balanced_journal_in_db(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    accounts = _accounts(client, auth_headers)
    voucher_type = client.post("/api/v1/voucher-types", headers=auth_headers, json={"name": "Invariant JV", "code": "IJV", "prefix": "IJV", "approval_required": True})
    assert voucher_type.status_code == 201, voucher_type.text
    voucher = client.post(
        "/api/v1/vouchers", headers=auth_headers,
        json={
            "voucher_type_id": voucher_type.json()["id"], "voucher_no": "IJV-00001", "narration": "Invariant voucher",
            "lines": [
                {"account_id": accounts["1000"]["id"], "debit": "40.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "40.00"},
            ],
        },
    )
    assert voucher.status_code == 201, voucher.text
    posted = client.post(f"/api/v1/vouchers/{voucher.json()['id']}/approve", headers=auth_headers)
    assert posted.status_code == 200, posted.text
    journal = _journal_for_source(db, company_id, "voucher", voucher.json()["id"])
    assert journal is not None
    _assert_journal_balanced(db, journal.id)


def test_payment_creates_balanced_journal_in_db(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    accounts = _accounts(client, auth_headers)
    payment = client.post(
        "/api/v1/payments", headers=auth_headers,
        json={"cash_bank_account_id": accounts["1000"]["id"], "debit_account_id": accounts["2100"]["id"], "payee_name": "Invariant Supplier", "amount": "22.00", "reference_no": "PAY-INV-001"},
    )
    assert payment.status_code == 201, payment.text
    assert payment.json()["status"] == "posted"
    voucher_id = payment.json()["voucher_id"]
    assert voucher_id
    journal = _journal_for_source(db, company_id, "voucher", voucher_id)
    assert journal is not None
    _assert_journal_balanced(db, journal.id)


def test_receipt_creates_balanced_journal_in_db(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    accounts = _accounts(client, auth_headers)
    receipt = client.post(
        "/api/v1/receipts", headers=auth_headers,
        json={"cash_bank_account_id": accounts["1000"]["id"], "credit_account_id": accounts["1100"]["id"], "received_from": "Invariant Customer", "amount": "18.00", "reference_no": "RCT-INV-001"},
    )
    assert receipt.status_code == 201, receipt.text
    assert receipt.json()["status"] == "posted"
    voucher_id = receipt.json()["voucher_id"]
    assert voucher_id
    journal = _journal_for_source(db, company_id, "voucher", voucher_id)
    assert journal is not None
    _assert_journal_balanced(db, journal.id)


def test_all_journals_balanced_after_mixed_posting_activity(client, db, auth_headers):
    """Belt-and-suspenders sweep: exercise several posting paths in one test,
    then verify EVERY journal for the company balances — catches a bug in a
    code path none of the targeted tests above happened to cover."""
    company_id = _company_id(client, auth_headers)
    accounts = _accounts(client, auth_headers)
    _post_and_approve(client, auth_headers, "purchase", "SRC-INV-MIX-001", "4000")
    _post_and_approve(client, auth_headers, "sales", "SRC-INV-MIX-002", "3000")
    client.post(
        "/api/v1/journal", headers=auth_headers,
        json={"entry_number": "JRN-INV-MIX-001", "description": "Mixed activity journal", "lines": [
            {"account_id": accounts["1000"]["id"], "debit": "10.00", "credit": "0"},
            {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "10.00"},
        ]},
    )
    client.post(
        "/api/v1/payments", headers=auth_headers,
        json={"cash_bank_account_id": accounts["1000"]["id"], "debit_account_id": accounts["2100"]["id"], "payee_name": "Mix Supplier", "amount": "5.00", "reference_no": "PAY-INV-MIX-001"},
    )
    _assert_all_journals_balanced(db, company_id)


# ── Category 2: Idempotency ────────────────────────────────────────────────


def test_double_approve_does_not_duplicate_journal(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-DUP-001", "4000")
    assert job["status"] == "posted"
    again = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)
    assert again.status_code == 202, again.text
    journal_count = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "purchase", JournalEntry.source_id == source["id"])
        .count()
    )
    assert journal_count == 1
    journal_id = _journal_for_source(db, company_id, "purchase", source["id"]).id
    gl_count = db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.journal_entry_id == journal_id).count()
    assert gl_count >= 1


def test_retry_already_posted_job_does_not_duplicate_journal(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-DUP-002", "4000")
    assert job["status"] == "posted"
    retried = client.post(f"/api/v1/posting-jobs/{job['id']}/retry", headers=auth_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "posted"
    journal_count = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "purchase", JournalEntry.source_id == source["id"])
        .count()
    )
    assert journal_count == 1


# ── Category 3: Reversal correctness ───────────────────────────────────────


def test_reversal_journal_is_balanced_and_cancels_original_per_account(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-REV-001", "4000")
    original = _journal_for_source(db, company_id, "purchase", source["id"])
    reversed_resp = client.post(f"/api/v1/journal/{original.id}/reverse", headers=auth_headers)
    assert reversed_resp.status_code == 200, reversed_resp.text
    reversal_id = reversed_resp.json()["id"]

    _assert_journal_balanced(db, reversal_id)

    net_by_account = (
        db.query(JournalLine.account_id, func.sum(JournalLine.debit) - func.sum(JournalLine.credit))
        .filter(JournalLine.journal_id.in_([original.id, reversal_id]))
        .group_by(JournalLine.account_id)
        .all()
    )
    for account_id, net in net_by_account:
        assert Decimal(net) == Decimal("0.00"), f"account {account_id} did not net to zero after reversal: {net}"


def test_reversal_does_not_mutate_or_delete_original_journal(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-REV-002", "4000")
    original = _journal_for_source(db, company_id, "purchase", source["id"])
    original_id = original.id
    original_status = original.status
    original_line_count = len(original.lines)

    reversed_resp = client.post(f"/api/v1/journal/{original_id}/reverse", headers=auth_headers)
    assert reversed_resp.status_code == 200, reversed_resp.text

    db.expire_all()
    still_there = db.query(JournalEntry).filter(JournalEntry.id == original_id).first()
    assert still_there is not None, "original journal must never be deleted by a reversal"
    assert still_there.status == original_status
    assert len(still_there.lines) == original_line_count


def test_reversing_a_reversal_is_rejected(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-REV-003", "4000")
    original = _journal_for_source(db, company_id, "purchase", source["id"])
    reversal = client.post(f"/api/v1/journal/{original.id}/reverse", headers=auth_headers)
    assert reversal.status_code == 200, reversal.text
    reversal_id = reversal.json()["id"]

    double_reversal = client.post(f"/api/v1/journal/{reversal_id}/reverse", headers=auth_headers)
    assert double_reversal.status_code == 400, double_reversal.text

    third_journal_count = db.query(JournalEntry).filter(JournalEntry.company_id == company_id, JournalEntry.source_id == reversal_id).count()
    assert third_journal_count == 0


def test_reversing_the_same_journal_twice_is_rejected(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-REV-004", "4000")
    original = _journal_for_source(db, company_id, "purchase", source["id"])
    first_reversal = client.post(f"/api/v1/journal/{original.id}/reverse", headers=auth_headers)
    assert first_reversal.status_code == 200, first_reversal.text

    second_attempt = client.post(f"/api/v1/journal/{original.id}/reverse", headers=auth_headers)
    assert second_attempt.status_code == 400, second_attempt.text

    reversal_count = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "reversal", JournalEntry.source_id == original.id)
        .count()
    )
    assert reversal_count == 1


# ── Category 4: Traceability ────────────────────────────────────────────────


def test_source_module_journal_traces_to_real_source_transaction(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _post_and_approve(client, auth_headers, "purchase", "SRC-INV-TRACE-001", "4000")
    _post_and_approve(client, auth_headers, "sales", "SRC-INV-TRACE-002", "3000")

    real_source_modules = {"sales", "sales_invoice", "purchase", "purchase_bill", "expense", "expenses"}
    journals = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module.in_(real_source_modules))
        .all()
    )
    assert journals, "expected at least the two just-posted journals"
    for journal in journals:
        source = db.query(SourceTransaction).filter(SourceTransaction.id == journal.source_id, SourceTransaction.company_id == company_id).first()
        assert source is not None, f"journal {journal.id} (source_module={journal.source_module}) has no matching SourceTransaction"


def test_posted_source_transaction_has_exactly_one_non_reversal_journal(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-TRACE-003", "4000")
    count = (
        db.query(JournalEntry)
        .filter(JournalEntry.company_id == company_id, JournalEntry.source_module == "purchase", JournalEntry.source_id == source["id"])
        .count()
    )
    assert count == 1


# ── Category 5: Tenant consistency (confirmatory — no known way to break this today) ──


def test_journal_and_gl_company_id_matches_source_transaction_company_id(client, db, auth_headers, second_tenant_headers):
    company_a = _company_id(client, auth_headers)
    company_b = _company_id(client, second_tenant_headers)

    source_a, _ = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-TENANT-A-001", "4000")
    source_b, _ = _post_and_approve(client, second_tenant_headers, "purchase", "SRC-INV-TENANT-B-001", "4000")

    journal_a = _journal_for_source(db, company_a, "purchase", source_a["id"])
    journal_b = _journal_for_source(db, company_b, "purchase", source_b["id"])
    assert journal_a is not None and journal_b is not None
    assert journal_a.company_id == company_a
    assert journal_b.company_id == company_b

    for journal in (journal_a, journal_b):
        gl_rows = db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.journal_entry_id == journal.id).all()
        assert gl_rows
        for row in gl_rows:
            assert row.company_id == journal.company_id


def test_cross_tenant_account_cannot_appear_in_a_journal_line(client, db, auth_headers, second_tenant_headers):
    from app.models import Account

    other_accounts = _accounts(client, second_tenant_headers)
    rejected = client.post(
        "/api/v1/journal", headers=auth_headers,
        json={
            "entry_number": "JRN-INV-XTENANT-001", "description": "Cross tenant attempt",
            "lines": [
                {"account_id": other_accounts["1100"]["id"], "debit": "100.00", "credit": "0"},
                {"account_id": other_accounts["3000"]["id"], "debit": "0", "credit": "100.00"},
            ],
        },
    )
    assert rejected.status_code == 422, rejected.text

    mismatches = (
        db.query(JournalLine.id)
        .join(JournalEntry, JournalEntry.id == JournalLine.journal_id)
        .join(Account, Account.id == JournalLine.account_id)
        .filter(JournalEntry.company_id != Account.company_id)
        .all()
    )
    assert not mismatches, f"found JournalLine rows whose account belongs to a different company: {mismatches}"


# ── Category 6: The two bug fixes (branch_id propagation, delete guard) ────


def test_manual_journal_with_explicit_branch_id_is_stamped_correctly(client, db):
    # Fresh tenant — creating a branch under the shared auth_headers tenant
    # would break test_branches.py's "a company that never creates a branch
    # sees zero" assumption, since that fixture's tenant is shared across
    # the whole pytest session, not just this file.
    headers = _fresh_company_admin_headers(client, db, "BRANCHA")
    branch = client.post("/api/v1/branches", headers=headers, json={"name": "Invariant Branch A"}).json()
    accounts = _accounts(client, headers)
    created = client.post(
        "/api/v1/journal", headers=headers,
        json={
            "entry_number": "JRN-INV-BRANCH-001", "description": "Branch-tagged manual journal", "branch_id": branch["id"],
            "lines": [
                {"account_id": accounts["1000"]["id"], "debit": "15.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "15.00"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    journal = db.query(JournalEntry).filter(JournalEntry.id == created.json()["id"]).first()
    assert journal.branch_id == branch["id"]
    gl_rows = db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.journal_entry_id == journal.id).all()
    assert gl_rows
    for row in gl_rows:
        assert row.branch_id == branch["id"]


def test_manual_journal_rejects_other_tenant_branch_id(client, db):
    headers = _fresh_company_admin_headers(client, db, "XBRANCH-OWN")
    other_headers = _fresh_company_admin_headers(client, db, "XBRANCH-OTHER")
    other_branch = client.post("/api/v1/branches", headers=other_headers, json={"name": "Other Tenant Branch"}).json()
    accounts = _accounts(client, headers)
    rejected = client.post(
        "/api/v1/journal", headers=headers,
        json={
            "entry_number": "JRN-INV-XBRANCH-001", "description": "Cross tenant branch attempt", "branch_id": other_branch["id"],
            "lines": [
                {"account_id": accounts["1000"]["id"], "debit": "5.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "5.00"},
            ],
        },
    )
    assert rejected.status_code == 422, rejected.text


def test_reversal_inherits_branch_id_from_original(client, db):
    headers = _fresh_company_admin_headers(client, db, "BRANCHB")
    company_id = _company_id(client, headers)
    branch = client.post("/api/v1/branches", headers=headers, json={"name": "Invariant Branch B"}).json()
    source, job = _post_and_approve(client, headers, "purchase", "SRC-INV-REVBRANCH-001", "4000", branch_id=branch["id"])
    original = _journal_for_source(db, company_id, "purchase", source["id"])
    assert original.branch_id == branch["id"]

    reversed_resp = client.post(f"/api/v1/journal/{original.id}/reverse", headers=headers)
    assert reversed_resp.status_code == 200, reversed_resp.text
    reversal = db.query(JournalEntry).filter(JournalEntry.id == reversed_resp.json()["id"]).first()
    assert reversal.branch_id == branch["id"]
    gl_rows = db.query(GeneralLedgerEntry).filter(GeneralLedgerEntry.journal_entry_id == reversal.id).all()
    assert gl_rows
    for row in gl_rows:
        assert row.branch_id == branch["id"]


def test_delete_rejects_posted_journal(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    source, job = _post_and_approve(client, auth_headers, "purchase", "SRC-INV-DELGUARD-001", "4000")
    journal = _journal_for_source(db, company_id, "purchase", source["id"])
    assert journal.status == "posted"

    rejected = client.delete(f"/api/v1/journal/{journal.id}", headers=auth_headers)
    assert rejected.status_code == 400, rejected.text

    still_there = db.query(JournalEntry).filter(JournalEntry.id == journal.id).first()
    assert still_there is not None


def test_clear_all_bypasses_posted_guard(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _post_and_approve(client, auth_headers, "purchase", "SRC-INV-CLEARALL-001", "4000")
    before_count = db.query(JournalEntry).filter(JournalEntry.company_id == company_id).count()
    assert before_count >= 1

    cleared = client.post("/api/v1/journal/clear-all", headers=auth_headers)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["deleted"] == before_count

    after_count = db.query(JournalEntry).filter(JournalEntry.company_id == company_id).count()
    assert after_count == 0
