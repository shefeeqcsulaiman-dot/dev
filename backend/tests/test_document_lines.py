"""document_lines (app/doc_lines.py): every sales invoice's lines as rows, kept current
on save/edit/delete (single and bulk), and the reads that use them -- stock movements
and the register's product filter -- give the same answers the JSON scan did."""
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import doc_lines
from app.models import AppDataRecord, DocumentLine
from tests.conftest import ensure_user


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"dl-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"92{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _save(client, headers, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": "salesInvoices", "record": record})
    assert r.status_code == 200, r.text


def _delete(client, headers, record):
    r = client.post("/api/v1/app-data?action=delete", headers=headers, json={"collection": "salesInvoices", "record": record})
    assert r.status_code == 200, r.text


def _old_stock_movements(db, company_id):
    """The JSON scan stock movements used before document_lines (reference behaviour)."""
    out = []
    rows = (db.query(AppDataRecord.payload)
            .filter(AppDataRecord.company_id == company_id, AppDataRecord.collection == "salesInvoices",
                    AppDataRecord.doc_kind == "invoice")
            .order_by(AppDataRecord.created_at, AppDataRecord.id).all())
    for (payload,) in rows:
        inv = json.loads(payload)
        if not inv.get("date") or str(inv.get("source") or "").lower().startswith("pos"):
            continue
        for line in inv["lines"] if isinstance(inv.get("lines"), list) else []:
            if not isinstance(line, dict):
                continue
            item = str(line.get("description") or line.get("product") or "").strip()
            try:
                qty = float(line.get("qty") or line.get("quantity") or 0)
            except (TypeError, ValueError):
                qty = 0
            if item and qty:
                out.append({"item_name": item, "movement_type": "sale", "quantity": -qty, "date": inv.get("date"),
                            "reference": inv.get("invoice_no") or "", "unit": line.get("unit") or "PCS"})
    return out


def _invoices():
    return [
        {"invoice_no": "DL-1", "customer": "Acme", "date": "2026-09-01", "total": 210,
         "lines": [{"description": "Widget", "unit": "BOX", "qty": 2, "unit_price": 100, "amount": 200},
                   {"description": "  Gadget  ", "qty": "1.5", "price": 10}]},
        {"invoice_no": "DL-2", "customer": "Beta", "date": "2026-09-02", "total": 50,
         "lines": [{"product": "Sprocket", "quantity": 5, "price": 10}, "not a line", {"description": "Freebie", "qty": 0}]},
        {"invoice_no": "DL-3", "customer": "Acme", "date": "2026-09-03", "total": 20, "source": "POS Terminal",
         "lines": [{"description": "Widget", "qty": 1, "price": 20}]},                       # POS: skipped
        {"invoice_no": "DL-4", "customer": "Acme", "total": 30,
         "lines": [{"description": "Widget", "qty": 3, "price": 10}]},                       # no date: skipped
        {"invoice_no": "DL-5", "customer": "Gamma", "date": "2026-09-05", "total": -10, "document_type": "Sales Return",
         "lines": [{"description": "Widget", "qty": 1, "price": 10}]},                       # return: skipped
        {"invoice_no": "DL-6", "customer": "Gamma", "date": "06/09/2026", "total": 99,
         "lines": [{"description": "Bolt", "product_name": "Steel Bolt M8", "qty": "abc", "price": 1},
                   {"description": "Nut", "qty": 4, "price": 0.5}]},
    ]


def test_stock_movements_match_the_old_json_scan(client, db, tenant):
    company_id, headers = tenant
    for inv in _invoices():
        _save(client, headers, inv)
    moves = client.get("/api/v1/app-data/sales-invoices/stock-movements", headers=headers).json()["movements"]
    key = lambda m: (m["reference"], m["item_name"])  # noqa: E731 -- same-second saves may swap
    assert sorted(moves, key=key) == sorted(_old_stock_movements(db, company_id), key=key)
    # Invoices saved in the same second may come back in either order (as before).
    assert sorted(m["item_name"] for m in moves) == ["Gadget", "Nut", "Sprocket", "Widget"]
    by_item = {m["item_name"]: m for m in moves}
    assert by_item["Widget"]["unit"] == "BOX" and by_item["Gadget"]["quantity"] == -1.5
    assert by_item["Sprocket"]["unit"] == "PCS" and by_item["Nut"]["date"] == "06/09/2026"
    assert doc_lines.verify_company(db, company_id)


def test_product_filter_matches_any_line_name_exactly(client, db, tenant):
    _, headers = tenant
    for inv in _invoices():
        _save(client, headers, inv)

    def numbers(product):
        r = client.get("/api/v1/app-data/sales-invoices", headers=headers, params={"product": product, "kind": "all", "limit": 50})
        assert r.status_code == 200, r.text
        return sorted(rec["invoice_no"] for rec in r.json()["records"])

    assert numbers("widget") == ["DL-1", "DL-3", "DL-4", "DL-5"]
    assert numbers("SPROCKET") == ["DL-2"]            # "product" field
    assert numbers("steel bolt m8") == ["DL-6"]       # "product_name" field
    assert numbers("widg") == []                      # whole names only
    assert numbers("  Gadget  ") == []                # stored as typed, so the padded name...
    assert numbers("gadget") == []                    # ...isn't "gadget" either (same as the JSON match)


def test_edit_and_delete_keep_lines_current(client, db, tenant):
    company_id, headers = tenant
    inv = _invoices()[0]
    _save(client, headers, inv)
    edited = {**inv, "lines": [{"description": "Replacement", "qty": 7, "price": 3}]}
    _save(client, headers, edited)
    names = lambda: sorted(n for (n,) in db.execute(  # noqa: E731
        select(DocumentLine.item_name).where(DocumentLine.company_id == company_id)))
    db.expire_all()
    assert names() == ["Replacement"]
    _delete(client, headers, edited)
    db.expire_all()
    assert names() == []


def test_bulk_delete_and_bulk_update_and_rebuild(db, tenant):
    company_id, _ = tenant
    rows = [AppDataRecord(company_id=company_id, collection="salesInvoices", record_key=f"BULK-{i}",
                          payload=json.dumps({"invoice_no": f"BULK-{i}", "date": "2026-09-09",
                                              "lines": [{"description": f"Item {i}", "qty": 1, "price": 5}]}))
            for i in range(3)]
    db.add_all(rows)
    db.commit()
    count = lambda: db.query(DocumentLine).filter(DocumentLine.company_id == company_id).count()  # noqa: E731
    assert count() == 3

    db.query(AppDataRecord).filter(AppDataRecord.record_key == "BULK-0", AppDataRecord.company_id == company_id).update(
        {AppDataRecord.payload: json.dumps({"invoice_no": "BULK-0", "date": "2026-09-09",
                                            "lines": [{"description": "A", "qty": 1}, {"description": "B", "qty": 2}]})},
        synchronize_session=False)
    db.commit()
    assert count() == 4

    db.query(AppDataRecord).filter(AppDataRecord.record_key.in_(["BULK-1", "BULK-2"]),
                                   AppDataRecord.company_id == company_id).delete(synchronize_session=False)
    db.commit()
    assert count() == 2
    assert doc_lines.verify_company(db, company_id)

    # A rebuild from the JSON gives the same rows.
    db.query(DocumentLine).filter(DocumentLine.company_id == company_id).delete(synchronize_session=False)
    db.commit()
    assert not doc_lines.verify_company(db, company_id)
    doc_lines.rebuild_company(db, company_id)
    db.commit()
    assert doc_lines.verify_company(db, company_id) and count() == 2


def test_record_moved_out_of_a_line_collection_loses_its_lines(db, tenant):
    company_id, _ = tenant
    row = AppDataRecord(company_id=company_id, collection="salesInvoices", record_key="MOVE-1",
                        payload=json.dumps({"invoice_no": "MOVE-1", "lines": [{"description": "X", "qty": 1}]}))
    db.add(row)
    db.commit()
    row.collection = "quotations"
    db.commit()
    assert db.query(DocumentLine).filter(DocumentLine.record_id == row.id).count() == 0
