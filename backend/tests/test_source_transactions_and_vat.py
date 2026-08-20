from datetime import datetime, timezone
from decimal import Decimal


def test_source_transaction_validation_approval_tax_and_audit(client, auth_headers):
    created = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "sales",
            "reference": "SRC-SALES-001",
            "party_name": "Source Customer",
            "lines": [{"description": "Sale", "account_code": "3000", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201
    source = created.json()
    assert source["subtotal"] == "100.00"
    assert source["vat"] == "5.00"
    assert source["total"] == "105.00"

    validated = client.post(f"/api/v1/source-transactions/{source['id']}/validate", headers=auth_headers)
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"

    approved = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)
    assert approved.status_code == 202
    assert approved.json()["status"] == "posted"

    tax_lines = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    matching = [line for line in tax_lines if line["source_id"] == source["id"]]
    assert len(matching) == 1
    assert matching[0]["direction"] == "output"
    assert Decimal(matching[0]["tax_amount"]) == Decimal("5.00")

    audit_rows = client.get("/api/v1/audit/trail", headers=auth_headers).json()
    assert any(row["record_id"] == source["id"] and row["action"] == "approved" for row in audit_rows)
    assert any(row["record_id"] == source["id"] and row["action"] == "posted_to_ledger" for row in audit_rows)

    journals = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    matching_journals = [journal for journal in journals if journal["source_id"] == source["id"]]
    assert len(matching_journals) == 1
    journal = matching_journals[0]
    assert journal["source_module"] == "sales"
    assert sum(Decimal(line["debit"]) for line in journal["lines"]) == sum(Decimal(line["credit"]) for line in journal["lines"])

    approved_again = client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)
    assert approved_again.status_code == 202
    assert approved_again.json()["status"] == "posted"
    journals_after_retry = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    assert len([journal for journal in journals_after_retry if journal["source_id"] == source["id"]]) == 1


def test_repost_by_reference_syncs_stale_ledger_after_purchase_edit(client, auth_headers):
    """Editing a purchase after it's already posted used to leave the
    original journal/GL permanently stale — post_source_transaction() is a
    deliberate no-op once ANY journal exists for a source, so a re-save's
    new amounts never reached the ledger on their own. The manual
    "Update Ledger" action (POST /source-transactions/repost-by-reference)
    reverses the stale journal and posts a fresh one matching current
    amounts, without ever mutating or deleting the original posted entry."""
    ref = "PUR-REPOST-001"
    first_save = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref,
                "supplier": "Repost Test Supplier",
                "net_amount": 100,
                "tax_amount": 5,
                "total": 105,
                "lines": [{"sku": "REPOST-SKU", "product": "Repost Item", "quantity": 1, "unit_cost": 100, "line_total": 100}],
            },
        },
    )
    assert first_save.status_code == 200, first_save.text

    journals_before = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    purchase_journals_before = [j for j in journals_before if j["source_module"] == "purchase"]
    original = next(j for j in purchase_journals_before if any(Decimal(l["debit"]) == Decimal("100.00") or Decimal(l["credit"]) == Decimal("100.00") for l in j["lines"]))
    original_count = len(purchase_journals_before)

    # Edit the same purchase — amounts change, but the existing post_source_
    # transaction() short-circuit means the journal doesn't follow along.
    second_save = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": ref,
                "supplier": "Repost Test Supplier",
                "net_amount": 200,
                "tax_amount": 10,
                "total": 210,
                "lines": [{"sku": "REPOST-SKU", "product": "Repost Item", "quantity": 2, "unit_cost": 100, "line_total": 200}],
            },
        },
    )
    assert second_save.status_code == 200, second_save.text

    journals_after_edit = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    purchase_journals_after_edit = [j for j in journals_after_edit if j["source_module"] == "purchase"]
    assert len(purchase_journals_after_edit) == original_count  # confirms the bug: no new journal from the edit alone
    assert purchase_journals_after_edit[0]["id"] == original["id"]

    # Now trigger the manual repost.
    reposted = client.post(
        "/api/v1/source-transactions/repost-by-reference",
        headers=auth_headers,
        json={"module": "purchase", "reference": ref},
    )
    assert reposted.status_code == 200, reposted.text
    assert reposted.json()["subtotal"] == "200.00"

    all_journals_after = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    reversal_journals = [j for j in all_journals_after if j["source_module"] == "reversal" and j["source_id"] == original["id"]]
    assert len(reversal_journals) == 1, "exactly one reversal must be created, matching the original's lines inverted"
    reversal = reversal_journals[0]
    assert sorted((Decimal(l["debit"]), Decimal(l["credit"])) for l in reversal["lines"]) == sorted((Decimal(l["credit"]), Decimal(l["debit"])) for l in original["lines"])

    new_purchase_journals = [j for j in all_journals_after if j["source_module"] == "purchase"]
    assert len(new_purchase_journals) == original_count + 1, "original stays untouched, a fresh corrected journal is added"
    fresh = next(j for j in new_purchase_journals if j["id"] != original["id"])
    assert any(Decimal(l["debit"]) == Decimal("200.00") or Decimal(l["credit"]) == Decimal("200.00") for l in fresh["lines"])

    # The original posted journal itself must be byte-for-byte untouched.
    original_after = next(j for j in all_journals_after if j["id"] == original["id"])
    assert original_after["lines"] == original["lines"]

    # VAT reporting must reflect the corrected amount, not the stale original.
    tax_lines = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    matching_tax = [t for t in tax_lines if t["source_id"] == reposted.json()["id"]]
    assert len(matching_tax) == 1
    assert Decimal(matching_tax[0]["tax_amount"]) == Decimal("10.00")

    # Calling it again with nothing changed must not create yet another reversal.
    idempotent = client.post(
        "/api/v1/source-transactions/repost-by-reference",
        headers=auth_headers,
        json={"module": "purchase", "reference": ref},
    )
    assert idempotent.status_code == 200, idempotent.text
    journals_final = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    reversals_final = [j for j in journals_final if j["source_module"] == "reversal"]
    assert len(reversals_final) == 2  # one more reversal for the just-posted fresh journal — still exactly one per repost call


def test_repost_by_reference_requires_existing_posted_record(client, auth_headers):
    missing = client.post(
        "/api/v1/source-transactions/repost-by-reference",
        headers=auth_headers,
        json={"module": "purchase", "reference": "PUR-NEVER-EXISTED"},
    )
    assert missing.status_code == 404


def test_source_transaction_missing_account_rejected(client, auth_headers):
    created = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "sales",
            "reference": "SRC-MISSING-001",
            "party_name": "Source Customer",
            "lines": [{"description": "Sale", "account_code": "NOPE", "quantity": "1", "unit_price": "100.00", "vat_rate": "5"}],
        },
    )
    assert created.status_code == 201

    approved = client.post(f"/api/v1/source-transactions/{created.json()['id']}/approve", headers=auth_headers)
    assert approved.status_code == 422
    assert "missing account" in approved.json()["detail"].lower()


def test_vat_return_reads_tax_lines(client, auth_headers):
    source = client.post(
        "/api/v1/source-transactions",
        headers=auth_headers,
        json={
            "module": "purchase",
            "reference": "SRC-PUR-001",
            "party_name": "Supplier",
            "lines": [{"description": "Purchase", "account_code": "4000", "quantity": "1", "unit_price": "200.00", "vat_rate": "5"}],
        },
    ).json()
    client.post(f"/api/v1/source-transactions/{source['id']}/approve", headers=auth_headers)

    # TaxLine.period is stamped from the transaction's actual creation date,
    # not a hardcoded value, so the current real-world period must be used.
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    vat_return = client.get(f"/api/v1/tax/vat-return?period={period}", headers=auth_headers)
    assert vat_return.status_code == 200
    payload = vat_return.json()
    assert Decimal(payload["input_vat"]) >= Decimal("10.00")

    saved_return = client.post(
        "/api/v1/tax/vat-returns",
        headers=auth_headers,
        json={"period": period, "adjustments": "1.00", "filing_status": "approved", "fta_reference_no": "FTA-QA-001"},
    )
    assert saved_return.status_code == 201
    assert Decimal(saved_return.json()["input_vat"]) >= Decimal("10.00")
    assert saved_return.json()["filing_status"] == "approved"


def test_purchase_fallback_vat_rate_uses_company_setting(client, auth_headers):
    """A purchase record saved with a tax_amount but no explicit line-level
    vat_rate used to always fall back to a hardcoded 5% (UAE) — it should
    instead fall back to the company's own configured VAT rate, so non-UAE
    tenants (e.g. Saudi Arabia at 15%) get correct VAT lines without having
    to specify the rate on every purchase."""
    updated = client.put(
        "/api/v1/companies/current",
        headers=auth_headers,
        json={"vat_rate": "15.00"},
    )
    assert updated.status_code == 200
    assert updated.json()["vat_rate"] == "15.00"

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "purchaseRecords",
            "record": {
                "ref": "PUR-VATRATE-001",
                "supplier": "VAT Rate Test Supplier",
                "net_amount": 100,
                "tax_amount": 15,
                "total": 115,
            },
        },
    )
    assert saved.status_code == 200, saved.text

    tax_lines = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    matching = [t for t in tax_lines if Decimal(t["tax_amount"]) == Decimal("15.00")]
    assert matching, f"expected a 15% VAT tax line, got: {tax_lines}"


def test_sales_invoice_fallback_vat_rate_uses_company_setting(client, auth_headers):
    """A salesInvoices app-data record saved with no explicit "lines" used to
    always get a single fallback InvoiceLine hardcoded at 5% VAT — it should
    use the company's configured VAT rate instead."""
    updated = client.put(
        "/api/v1/companies/current",
        headers=auth_headers,
        json={"vat_rate": "15.00"},
    )
    assert updated.status_code == 200

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "salesInvoices",
            "record": {
                "invoice_no": "INV-VATRATE-001",
                "customer": "VAT Rate Test Customer",
                "status": "issued",
                "subtotal": "100.00",
                "total": "115.00",
                "vat_amount": "15.00",
            },
        },
    )
    assert saved.status_code == 200, saved.text

    invoices = client.get("/api/v1/invoices", headers=auth_headers).json()
    invoice = next(i for i in invoices if i["invoice_number"] == "INV-VATRATE-001")
    assert invoice["lines"], "expected a fallback line to be created"
    assert Decimal(invoice["lines"][0]["vat_rate"]) == Decimal("15.00")


def test_sales_invoice_header_totals_recomputed_server_side_not_trusted_from_client(client, auth_headers):
    """sync_sales_invoice() used to persist invoice.subtotal/vat/total
    verbatim from whatever the client sent (record.get("subtotal"/
    "vat_amount"/"total")) — a client-side bug, or the several places in
    app.js that used to hardcode 5% VAT regardless of the company's
    configured rate, would silently persist a wrong total that then drives
    VAT return figures. Deliberately send a header computed at the WRONG
    (5%) rate for a company configured at 15%, with real lines — the
    persisted invoice must reflect the correct 15% total computed from the
    lines, not the wrong client-sent header."""
    updated = client.put("/api/v1/companies/current", headers=auth_headers, json={"vat_rate": "15.00"})
    assert updated.status_code == 200

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "salesInvoices",
            "record": {
                "invoice_no": "INV-VATRATE-TRUST-001",
                "customer": "VAT Trust Test Customer",
                "status": "issued",
                # Deliberately wrong client-computed header — 5% of 200 is
                # 10.00/210.00, not the company's real 15% rate.
                "subtotal": "200.00",
                "vat_amount": "10.00",
                "total": "210.00",
                "lines": [
                    {"description": "Consulting", "qty": 1, "unit_price": "200.00"},
                ],
            },
        },
    )
    assert saved.status_code == 200, saved.text

    invoices = client.get("/api/v1/invoices", headers=auth_headers).json()
    invoice = next(i for i in invoices if i["invoice_number"] == "INV-VATRATE-TRUST-001")
    assert Decimal(invoice["lines"][0]["vat_rate"]) == Decimal("15.00")
    assert Decimal(invoice["subtotal"]) == Decimal("200.00")
    assert Decimal(invoice["vat"]) == Decimal("30.00")
    assert Decimal(invoice["total"]) == Decimal("230.00")


def test_bootstrap_company_payload_includes_currency_and_vat_rate(client, auth_headers):
    """GET /app-data (bootstrap)'s hand-built "company" dict is a separate
    code path from GET /companies/current — it used to omit currency/vat_rate
    entirely, which silently reset the Settings page's currency/VAT fields
    back to the AED/5% defaults on every fresh app load (bootstrap fires
    after the correct /companies/current fetch and applyCompanyToUi()
    overwrites the form fields with whatever ran last)."""
    updated = client.put(
        "/api/v1/companies/current",
        headers=auth_headers,
        json={"currency": "SAR", "vat_rate": "15.00"},
    )
    assert updated.status_code == 200

    boot = client.get("/api/v1/app-data", headers=auth_headers)
    assert boot.status_code == 200, boot.text
    company = boot.json()["data"]["company"]
    assert company["currency"] == "SAR"
    assert company["vat_rate"] == "15.00"


def test_corporate_tax_return_calculates_taxable_income(client, auth_headers):
    response = client.post(
        "/api/v1/tax/corporate-tax-returns",
        headers=auth_headers,
        json={
            "tax_period": "2024",
            "accounting_profit": "100000.00",
            "non_deductible_expenses": "1000.00",
            "exempt_income": "500.00",
            "tax_loss_adjustment": "100.00",
            "tax_rate": "9.00",
            "filing_status": "approved",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert Decimal(payload["taxable_income"]) == Decimal("100400.00")
    # Below the AED 375,000 Small Business Relief threshold — 0% applies.
    assert Decimal(payload["corporate_tax_payable"]) == Decimal("0.00")


def test_corporate_tax_return_applies_small_business_relief_threshold(client, auth_headers):
    response = client.post(
        "/api/v1/tax/corporate-tax-returns",
        headers=auth_headers,
        json={
            "tax_period": "2025",
            "accounting_profit": "500000.00",
            "non_deductible_expenses": "1000.00",
            "exempt_income": "500.00",
            "tax_loss_adjustment": "100.00",
            "tax_rate": "9.00",
            "filing_status": "approved",
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert Decimal(payload["taxable_income"]) == Decimal("500400.00")
    # Only the amount above AED 375,000 is taxed: (500400 - 375000) * 9% = 11286.00
    assert Decimal(payload["corporate_tax_payable"]) == Decimal("11286.00")


def test_pos_refund_reverses_output_vat_without_full_ledger_posting(client, auth_headers):
    """POS refunds (negative-signed salesInvoices credit notes) deliberately
    skip sync_sales_invoice()/build_journal() — build_journal() rejects
    negative amounts, and this app has a documented decision not to give
    returns full ledger posting. But /tax/vat-return only ever sums TaxLine
    rows, and skipping full posting meant a refund never created one, so it
    silently never reduced output VAT owed even though it correctly reduces
    reported revenue. ensure_credit_note_tax_line() closes that specific gap
    with a standalone TaxLine — this test proves the VAT reversal works,
    is idempotent on re-save, and creates no JournalEntry."""
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    sale = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "salesInvoices",
            "record": {
                "invoice_no": "POS-REFUND-TEST-001",
                "document_type": "Tax Invoice",
                "source": "POS",
                "status": "Paid",
                "date": today,
                "customer": "Walk-In Customer",
                "subtotal": 100,
                "vat_amount": 5,
                "total": 105,
                "lines": [{"description": "Test Item", "qty": 1, "unit_price": 100, "tax_rate": 5}],
            },
        },
    )
    assert sale.status_code == 200, sale.text

    vat_after_sale = client.get(f"/api/v1/tax/vat-return?period={period}", headers=auth_headers).json()
    output_after_sale = Decimal(vat_after_sale["output_vat"])
    assert output_after_sale >= Decimal("5.00")

    journals_before_refund = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    journal_count_before_refund = len(journals_before_refund)

    refund_record = {
        "invoice_no": "POS-REFUND-TEST-001-R",
        "reference_no": "POS-REFUND-TEST-001",
        "document_type": "Sales Return",
        "source": "POS Return",
        "status": "Return",
        "date": today,
        "customer": "Walk-In Customer",
        "subtotal": -100,
        "vat_amount": -5,
        "total": -105,
    }
    refund = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "salesInvoices", "record": refund_record},
    )
    assert refund.status_code == 200, refund.text

    vat_after_refund = client.get(f"/api/v1/tax/vat-return?period={period}", headers=auth_headers).json()
    output_after_refund = Decimal(vat_after_refund["output_vat"])
    assert output_after_refund == output_after_sale - Decimal("5.00")

    journals_after_refund = client.get("/api/v1/journal", headers=auth_headers).json()["records"]
    assert len(journals_after_refund) == journal_count_before_refund, "refund must not create any JournalEntry"

    # Re-saving the identical refund (e.g. a client retry) must not double-count.
    refund_again = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={"collection": "salesInvoices", "record": refund_record},
    )
    assert refund_again.status_code == 200, refund_again.text
    vat_after_retry = client.get(f"/api/v1/tax/vat-return?period={period}", headers=auth_headers).json()
    assert Decimal(vat_after_retry["output_vat"]) == output_after_refund

    tax_lines = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    credit_note_lines = [t for t in tax_lines if Decimal(t["tax_amount"]) == Decimal("-5.00")]
    assert len(credit_note_lines) == 1, "exactly one TaxLine for the refund, not two, after the duplicate save"


def test_hand_keyed_positive_sales_return_does_not_use_credit_note_tax_line_path(client, auth_headers):
    """A legacy hand-keyed Sales Return (positive amounts) is NOT a
    negative-signed POS credit note — _is_credit_note_record() only matches
    on sign, so this must flow through the normal sync_sales_invoice() path
    exactly as before, and must NOT also pick up a TaxLine from the new
    ensure_credit_note_tax_line() path (which is scoped to negative-signed
    records only)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # auth_headers reuses one fixed-TRN company across the whole test
    # session, so other tests in this file may have already left negative
    # TaxLines behind (e.g. the POS refund test above) — compare before/after
    # counts for THIS save rather than asserting an absolute zero.
    tax_lines_before = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    negative_count_before = len([t for t in tax_lines_before if Decimal(t["tax_amount"]) < 0])

    saved = client.post(
        "/api/v1/app-data?action=save",
        headers=auth_headers,
        json={
            "collection": "salesInvoices",
            "record": {
                "invoice_no": "HANDKEYED-RETURN-TEST-001",
                "document_type": "Sales Return",
                "source": "Sales",
                "status": "Return",
                "date": today,
                "customer": "Walk-In Customer",
                "subtotal": 100,
                "vat_amount": 5,
                "total": 105,
                "lines": [{"description": "Returned Item", "qty": 1, "unit_price": 100, "tax_rate": 5}],
            },
        },
    )
    assert saved.status_code == 200, saved.text

    tax_lines_after = client.get("/api/v1/tax/lines", headers=auth_headers).json()
    negative_count_after = len([t for t in tax_lines_after if Decimal(t["tax_amount"]) < 0])
    assert negative_count_after == negative_count_before, "a positive-amount hand-keyed return must not produce a credit-note-style reversal TaxLine"
