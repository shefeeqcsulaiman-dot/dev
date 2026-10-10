"""catalog_products: every product record has a typed row kept current on save, edit and
delete (app/catalog_products.py), and the picker search reads it (so field names in the
JSON no longer match)."""
import json
from decimal import Decimal
from uuid import uuid4

from app.catalog_products import mirror, verify_company
from app.models import AppDataRecord, CatalogProduct, User


def _company_id(db):
    return db.query(User.company_id).filter(User.email == "qa-admin@taxflowqa.com").scalar()


def _search(client, headers, collection, q):
    return client.get(f"/api/v1/app-data/catalog/{collection}", headers=headers, params={"q": q}).json()["records"]


def test_rows_follow_saves_edits_and_deletes(client, db, auth_headers):
    tag = uuid4().hex[:6].upper()
    code = f"CP-{tag}"
    r = client.post("/api/v1/app-data", headers=auth_headers, params={"action": "save"}, json={
        "collection": "products",
        "record": {"code": code, "name": f"Copper Pipe {tag}", "unit": "M", "selling_price": "12.50", "cost": 8,
                   "supplier_name": "Pipes LLC", "category": "Plumbing"}})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.query(CatalogProduct).filter(CatalogProduct.code == code).one()
    assert (row.name_key, row.code_key, row.unit, row.selling_price, row.cost) == (
        f"copper pipe {tag.lower()}", code.lower(), "M", Decimal("12.50"), Decimal("8.00"))

    rec = db.query(AppDataRecord).filter(AppDataRecord.id == row.id).one()
    rec.payload = json.dumps({"code": code, "name": f"Copper Tube {tag}", "price": "15"})
    db.commit()
    db.expire_all()
    row = db.get(CatalogProduct, rec.id)
    assert row.name == f"Copper Tube {tag}" and row.selling_price == Decimal("15.00")

    db.delete(db.get(AppDataRecord, rec.id))
    db.commit()
    assert db.get(CatalogProduct, rec.id) is None
    assert verify_company(db, _company_id(db))


def test_rebuild_restores_missing_rows(db, auth_headers):
    cid = _company_id(db)
    tag = uuid4().hex[:6]
    db.add(AppDataRecord(company_id=cid, collection="products", record_key=f"RB-{tag}",
                         payload=json.dumps({"code": f"RB-{tag}", "name": "Rebuild Me"})))
    db.commit()
    db.query(CatalogProduct).filter(CatalogProduct.company_id == cid).delete()
    db.commit()
    assert not verify_company(db, cid)
    mirror.rebuild_company(db, cid)
    db.commit()
    assert verify_company(db, cid)


def test_search_matches_fields_not_json_keys(client, db, auth_headers):
    cid = _company_id(db)
    tag = uuid4().hex[:6]
    db.add_all([
        AppDataRecord(company_id=cid, collection="products", record_key=f"KW-{tag}",
                      payload=json.dumps({"code": f"KW-{tag}", "name": f"Kettle {tag}", "unit": "PCS",
                                          "supplier_name": f"Brightway {tag}", "category": "Kitchen"})),
        AppDataRecord(company_id=cid, collection="customers", record_key=f"Nadia {tag}",
                      payload=json.dumps({"name": f"Nadia {tag}", "trn": "100-345-678-900003",
                                          "email": f"nadia-{tag}@shop.example", "phone": "+971 50 123 4567"})),
    ])
    db.commit()
    # Field names that every record's JSON contains match nothing by themselves.
    for word in ("name", "code", "unit", "supplier_name"):
        assert not any(tag in (p.get("code") or "") for p in _search(client, auth_headers, "products", word)), word
    for word in ("email", "phone", "trn"):
        assert not any(tag in (c.get("name") or "") for c in _search(client, auth_headers, "customers", word)), word
    # Real values still do.
    assert [p["code"] for p in _search(client, auth_headers, "products", f"brightway {tag}")] == [f"KW-{tag}"]
    assert [p["code"] for p in _search(client, auth_headers, "products", f"kw-{tag}")] == [f"KW-{tag}"]
    names = [c["name"] for c in _search(client, auth_headers, "customers", "100345678")]
    assert f"Nadia {tag}" in names  # TRN digits, whatever punctuation was saved
    assert [c["name"] for c in _search(client, auth_headers, "customers", f"nadia-{tag}@")] == [f"Nadia {tag}"]
