from decimal import Decimal


def _accounts(client, headers):
    return {row["code"]: row for row in client.get("/api/v1/accounts", headers=headers).json()}


def test_unbalanced_journal_is_rejected(client, auth_headers):
    accounts = _accounts(client, auth_headers)

    response = client.post(
        "/api/v1/journal",
        headers=auth_headers,
        json={
            "entry_number": "JRN-BAD-001",
            "description": "Bad journal",
            "lines": [
                {"account_id": accounts["1100"]["id"], "debit": "100.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "90.00"},
            ],
        },
    )

    assert response.status_code == 422
    assert "balance" in response.json()["detail"].lower()


def test_balanced_journal_is_accepted(client, auth_headers):
    accounts = _accounts(client, auth_headers)

    response = client.post(
        "/api/v1/journal",
        headers=auth_headers,
        json={
            "entry_number": "JRN-OK-001",
            "description": "Balanced journal",
            "lines": [
                {"account_id": accounts["1100"]["id"], "debit": "105.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "100.00"},
                {"account_id": accounts["2200"]["id"], "debit": "0", "credit": "5.00"},
            ],
        },
    )

    assert response.status_code == 201
    lines = response.json()["lines"]
    assert sum(float(line["debit"]) for line in lines) == sum(float(line["credit"]) for line in lines)


def test_journal_rejects_other_tenant_account(client, auth_headers, second_tenant_headers):
    other_accounts = _accounts(client, second_tenant_headers)

    response = client.post(
        "/api/v1/journal",
        headers=auth_headers,
        json={
            "entry_number": "JRN-XTENANT-001",
            "description": "Cross tenant account",
            "lines": [
                {"account_id": other_accounts["1100"]["id"], "debit": "100.00", "credit": "0"},
                {"account_id": other_accounts["3000"]["id"], "debit": "0", "credit": "100.00"},
            ],
        },
    )

    assert response.status_code == 422
    assert "company" in response.json()["detail"].lower()


def test_failed_posting_job_can_be_retried_after_mapping_fix(client, auth_headers):
    accounts = _accounts(client, auth_headers)
    deleted = client.delete(f"/api/v1/accounts/{accounts['2100']['id']}", headers=auth_headers)
    assert deleted.status_code == 204

    created = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "purchase",
            "reference": "SRC-RETRY-001",
            "party_name": "Retry Supplier",
            "lines": [{"description": "Purchase", "account_code": "4000", "quantity": "1", "unit_price": "200.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201

    approved = client.post(f"/api/v1/source-transactions/{created.json()['id']}/approve", headers=auth_headers)
    assert approved.status_code == 202
    failed_job = approved.json()
    assert failed_job["status"] == "failed"
    assert "2100" in failed_job["error_message"]

    recreated = client.post(
        "/api/v1/accounts",
        headers=auth_headers,
        json={"code": "2100", "name": "Accounts Payable", "type": "liability"},
    )
    assert recreated.status_code == 201

    retried = client.post(f"/api/v1/posting-jobs/{failed_job['id']}/retry", headers=auth_headers)
    assert retried.status_code == 200
    assert retried.json()["status"] == "posted"

    journals = client.get("/api/v1/journal", headers=auth_headers).json()
    matching = [journal for journal in journals if journal["source_id"] == created.json()["id"]]
    assert len(matching) == 1


def test_voucher_payment_receipt_gl_bank_and_period_lock(client, auth_headers):
    accounts = _accounts(client, auth_headers)
    voucher_type = client.post(
        "/api/v1/voucher-types",
        headers=auth_headers,
        json={"name": "Journal Voucher QA", "code": "JQA", "prefix": "JQA", "approval_required": True},
    )
    assert voucher_type.status_code == 201

    voucher = client.post(
        "/api/v1/vouchers",
        headers=auth_headers,
        json={
            "voucher_type_id": voucher_type.json()["id"],
            "voucher_no": "JQA-00001",
            "narration": "Opening test adjustment",
            "lines": [
                {"account_id": accounts["1000"]["id"], "debit": "50.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "50.00"},
            ],
        },
    )
    assert voucher.status_code == 201

    posted = client.post(f"/api/v1/vouchers/{voucher.json()['id']}/approve", headers=auth_headers)
    assert posted.status_code == 200
    assert posted.json()["status"] == "posted"
    gl = client.get("/api/v1/general-ledger", headers=auth_headers).json()
    assert len([row for row in gl if row["voucher_no"] == "JQA-00001"]) == 2

    payment = client.post(
        "/api/v1/payments",
        headers=auth_headers,
        json={
            "cash_bank_account_id": accounts["1000"]["id"],
            "debit_account_id": accounts["2100"]["id"],
            "payee_name": "Supplier QA",
            "amount": "25.00",
            "reference_no": "PAY-QA-001",
        },
    )
    assert payment.status_code == 201
    assert payment.json()["status"] == "posted"

    receipt = client.post(
        "/api/v1/receipts",
        headers=auth_headers,
        json={
            "cash_bank_account_id": accounts["1000"]["id"],
            "credit_account_id": accounts["1100"]["id"],
            "received_from": "Customer QA",
            "amount": "30.00",
            "reference_no": "RCT-QA-001",
        },
    )
    assert receipt.status_code == 201
    assert receipt.json()["status"] == "posted"

    bank = client.post(
        "/api/v1/bank-accounts",
        headers=auth_headers,
        json={"account_id": accounts["1000"]["id"], "bank_name": "QA Bank", "account_number": "12345"},
    )
    assert bank.status_code == 201
    statement = client.post(
        "/api/v1/bank-statement-lines",
        headers=auth_headers,
        json={
            "bank_account_id": bank.json()["id"],
            "statement_date": "2024-06-30",
            "transaction_date": "2024-06-15",
            "reference_no": "PAY-QA-001",
            "credit": "0",
            "debit": "25.00",
        },
    )
    assert statement.status_code == 201
    bank_gl = client.get(f"/api/v1/general-ledger?account_id={accounts['1000']['id']}", headers=auth_headers).json()
    ledger_entry = next(row for row in bank_gl if row["voucher_no"] == payment.json()["payment_no"] and Decimal(row["credit"]) == Decimal("25.00"))
    matched = client.post(
        "/api/v1/bank-reconciliation/matches",
        headers=auth_headers,
        json={"statement_line_id": statement.json()["id"], "ledger_entry_id": ledger_entry["id"]},
    )
    assert matched.status_code == 201
    assert matched.json()["match_status"] == "matched"

    locked = client.post(
        "/api/v1/period-locks",
        headers=auth_headers,
        json={"module": "accounting", "period": "2024-06", "status": "locked", "reason": "Month closed"},
    )
    assert locked.status_code == 201
    blocked = client.post(
        "/api/v1/vouchers",
        headers=auth_headers,
        json={
            "voucher_type_id": voucher_type.json()["id"],
            "voucher_no": "JQA-LOCKED",
            "voucher_date": "2024-06-20T00:00:00Z",
            "lines": [
                {"account_id": accounts["1000"]["id"], "debit": "1.00", "credit": "0"},
                {"account_id": accounts["3000"]["id"], "debit": "0", "credit": "1.00"},
            ],
        },
    )
    assert blocked.status_code == 423
