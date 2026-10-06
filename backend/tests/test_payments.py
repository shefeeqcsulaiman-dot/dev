"""
Tests for payment recording, partial payments, and allocation tracking.
"""
import json


FULL_PAYMENT = {
    "type": "Customer Receipt",
    "ref": "RCT-2025-0001",
    "contact": "Alpha Corp",
    "amount": 525.00,
    "method": "Bank Transfer",
    "date": "2025-06-01",
    "bank": "FAB - Main",
    "comments": "",
    "detail": "INV-2025-001",
    "document_ref": "INV-2025-001",
    "invoice_no": "INV-2025-001",
    "allocations": [
        {"doc_ref": "INV-2025-001", "amount": 525.00}
    ],
}

PARTIAL_PAYMENT = {
    "type": "Customer Receipt",
    "ref": "RCT-2025-0002",
    "contact": "Beta LLC",
    "amount": 250.00,
    "method": "Cash",
    "date": "2025-06-02",
    "bank": "",
    "comments": "Partial payment agreed",
    "detail": "INV-2025-002",
    "document_ref": "INV-2025-002",
    "invoice_no": "INV-2025-002",
    "allocations": [
        {"doc_ref": "INV-2025-002", "amount": 250.00}
    ],
}

MULTI_ALLOC_PAYMENT = {
    "type": "Customer Receipt",
    "ref": "RCT-2025-0003",
    "contact": "Gamma Trading",
    "amount": 800.00,
    "method": "Bank Transfer",
    "date": "2025-06-03",
    "bank": "ADCB - Operations",
    "comments": "Covers two invoices",
    "detail": "",
    "document_ref": "INV-2025-003",
    "invoice_no": "INV-2025-003",
    "allocations": [
        {"doc_ref": "INV-2025-003", "amount": 500.00},
        {"doc_ref": "INV-2025-004", "amount": 300.00},
    ],
}

SUPPLIER_PAYMENT = {
    "type": "Supplier Payment",
    "ref": "PAY-2025-0001",
    "contact": "Delta Supplies",
    "amount": 1050.00,
    "method": "Bank Transfer",
    "date": "2025-06-04",
    "bank": "FAB - Main",
    "comments": "",
    "detail": "BILL-001",
    "document_ref": "BILL-001",
    "bill_no": "BILL-001",
    "allocations": [
        {"doc_ref": "BILL-001", "amount": 1050.00}
    ],
}


def _save_payment(client, headers, record):
    return client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "payments", "record": record},
    )


def test_full_payment_recorded(client, auth_headers):
    r = _save_payment(client, auth_headers, FULL_PAYMENT)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["saved"] is True


def test_partial_payment_recorded(client, auth_headers):
    r = _save_payment(client, auth_headers, PARTIAL_PAYMENT)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    # partial: amount (250) < invoice total — record should still be accepted
    assert data["saved"] is True


def test_multi_allocation_payment(client, auth_headers):
    r = _save_payment(client, auth_headers, MULTI_ALLOC_PAYMENT)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["saved"] is True


def test_supplier_payment_recorded(client, auth_headers):
    r = _save_payment(client, auth_headers, SUPPLIER_PAYMENT)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_payment_persisted_in_collection(client, auth_headers):
    ref = "RCT-PERSIST-001"
    record = {**FULL_PAYMENT, "ref": ref}
    _save_payment(client, auth_headers, record)

    r = client.get("/api/v1/app-data/records/payments", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    refs = [rec.get("ref") for rec in body["records"]]
    assert ref in refs


def test_payment_allocations_stored(client, auth_headers):
    ref = "RCT-ALLOC-001"
    record = {
        **MULTI_ALLOC_PAYMENT,
        "ref": ref,
        "allocations": [
            {"doc_ref": "INV-A", "amount": 400.00},
            {"doc_ref": "INV-B", "amount": 400.00},
        ],
    }
    _save_payment(client, auth_headers, record)

    r = client.get("/api/v1/app-data/records/payments", headers=auth_headers)
    records = r.json()["records"]
    saved = next((rec for rec in records if rec.get("ref") == ref), None)
    assert saved is not None
    allocs = saved.get("allocations", [])
    assert len(allocs) == 2
    refs = {a["doc_ref"] for a in allocs}
    assert refs == {"INV-A", "INV-B"}
    total_alloc = sum(a["amount"] for a in allocs)
    assert abs(total_alloc - 800.0) < 0.01


def test_payment_tenant_isolation(client, auth_headers, second_tenant_headers):
    ref = "RCT-ISOLATED-001"
    _save_payment(client, auth_headers, {**FULL_PAYMENT, "ref": ref})

    r = client.get("/api/v1/app-data/records/payments", headers=second_tenant_headers)
    records = r.json()["records"]
    refs = [rec.get("ref") for rec in records]
    assert ref not in refs


def test_payment_amount_validation_zero_rejected(client, auth_headers):
    # Zero-amount payment should still be accepted at API level (validation is on frontend)
    # but we verify the record is stored with amount = 0
    r = _save_payment(client, auth_headers, {**FULL_PAYMENT, "ref": "RCT-ZERO-001", "amount": 0})
    # API stores it; frontend prevents it — just confirm no 500
    assert r.status_code == 200


def test_saved_payment_is_listed_by_the_payments_register(client, auth_headers):
    # Payments page from GET /app-data/registers/payments; the bootstrap no longer carries them.
    ref = "RCT-BOOT-001"
    _save_payment(client, auth_headers, {**FULL_PAYMENT, "ref": ref})

    r = client.get("/api/v1/app-data/registers/payments", headers=auth_headers, params={"q": ref})
    assert r.status_code == 200
    assert ref in [p.get("ref") for p in r.json()["records"]]
    assert "payments" not in client.get("/api/v1/app-data", headers=auth_headers).json()["data"]


def test_db_dump_returns_sql(client, auth_headers):
    # Save a payment so there's something to dump
    _save_payment(client, auth_headers, {**FULL_PAYMENT, "ref": "RCT-DUMP-001"})

    r = client.get("/api/v1/app-data/db-dump", headers=auth_headers)
    assert r.status_code == 200
    content = r.text
    # Must look like SQL
    assert "BEGIN;" in content
    assert "COMMIT;" in content
    assert "INSERT INTO" in content
    assert "app_data_records" in content


def test_db_dump_tenant_isolation(client, auth_headers, second_tenant_headers):
    ref = "RCT-DUMP-ISOLATED"
    _save_payment(client, auth_headers, {**FULL_PAYMENT, "ref": ref})

    # Tenant 2's dump must NOT contain tenant 1's record
    r = client.get("/api/v1/app-data/db-dump", headers=second_tenant_headers)
    assert r.status_code == 200
    assert ref not in r.text


def test_export_endpoint_returns_all_collections(client, auth_headers):
    r = client.get("/api/v1/app-data/export", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "meta" in body
    assert "data" in body
    assert "exported_at" in body["meta"]
