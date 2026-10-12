"""catalog_products is the main copy of products (step C): the row holds the whole product
except its inline image, saves store the JSON the row regenerates, the image is kept."""
import json

from app import catalog_products
from app.catalog_products import canonical_product, product_fields, product_record
from app.models import AppDataRecord, CatalogProduct
from tests.test_party_checks import _bulk, _save, tenant  # noqa: F401  (fixture)

IMG = "data:image/png;base64,iVBORw0KGgo="


def test_round_trip_keeps_every_field_and_the_image():
    doc = {"code": " ABC-1 ", "name": "Cola Can", "price": "2.50", "cost": "1,200", "sku": "ABC-1", "image": IMG,
           "reorder_level": 5, "description": "330ml", "barcode": "", "supplier": "Gulf Bev"}
    out = canonical_product(doc)
    assert out == {"name": "Cola Can", "code": "ABC-1", "selling_price": 2.5, "cost": 1200.0, "supplier_name": "Gulf Bev",
                   "barcode": "", "price": "2.50", "sku": "ABC-1", "reorder_level": 5, "description": "330ml",
                   "supplier": "Gulf Bev", "image": IMG}
    assert canonical_product(out) == out
    fields = product_fields(doc)
    assert IMG not in (fields["extra"] or "")                    # the image is not copied into the row
    assert product_record(fields, IMG) == out


def test_saves_store_what_the_row_regenerates(client, db, tenant):  # noqa: F811
    company_id, h = tenant
    assert _save(client, h, "products", {"code": "P-1", "name": "Rice", "selling_price": "12", "image": IMG,
                                         "min_stock": 2, "tracking": "Yes"}).status_code == 200
    _bulk(client, h, "products", [{"code": "P-2", "name": "Sugar", "price": 4, "category": "Dry"}])
    db.expire_all()
    for code in ("P-1", "P-2"):
        row = db.query(CatalogProduct).filter(CatalogProduct.company_id == company_id, CatalogProduct.code == code).one()
        stored = json.loads(db.get(AppDataRecord, row.id).payload)
        assert product_record(row, stored.get("image")) == stored, code
    rice = db.query(CatalogProduct).filter(CatalogProduct.company_id == company_id, CatalogProduct.code == "P-1").one()
    assert rice.selling_price == 12 and json.loads(rice.extra) == {"min_stock": 2}
    assert catalog_products.verify_company(db, company_id) and catalog_products.stale_json(db, company_id) == []

    # A product written around the save path is found and rewritten, image kept.
    rec = db.get(AppDataRecord, rice.id)
    rec.payload = json.dumps({"code": "P-1", "name": "Rice", "price": "13", "image": IMG})
    db.commit()
    assert catalog_products.stale_json(db, company_id) == [rec.id]
    catalog_products.rewrite_json(db, [rec.id])
    db.commit()
    assert json.loads(db.get(AppDataRecord, rec.id).payload) == {"name": "Rice", "code": "P-1", "selling_price": 13.0,
                                                                 "price": "13", "image": IMG}
