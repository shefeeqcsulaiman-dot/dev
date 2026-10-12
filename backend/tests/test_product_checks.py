"""Products are checked before they're saved (app/product_checks.py): a form save is refused
with a clear message; an import, or a single save marked lenient, cleans and carries on."""
from app.models import CatalogProduct
from tests.test_party_checks import _bulk, _save, tenant  # noqa: F401  (fixture)


def _lenient(client, headers, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers,
                    json={"collection": "products", "record": record, "lenient": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_form_saves_are_refused_with_a_reason(client, db, tenant):  # noqa: F811
    _, h = tenant
    assert _save(client, h, "products", {"code": " ABC-1 ", "name": "Cola Can", "selling_price": "2.50",
                                         "cost": "1,200.50", "barcode": "6291000000017"}).status_code == 200
    for record, code, text in (
        ({"code": "X-1", "name": " "}, 422, "Name is required."),
        ({"code": "X-1", "name": "Water", "cost": "abc"}, 422, "Cost must be a number."),
        ({"code": "X-1", "name": "Water", "price": -1}, 422, "Price can't be negative."),
        ({"code": "X-1", "name": "Water", "min_stock": 50, "max_stock": 10}, 422, "Minimum stock is more than the maximum."),
        ({"code": "abc-1", "name": "Water"}, 409, 'Item code "abc-1" is already used by "Cola Can".'),
        ({"code": "X-1", "name": "Water", "barcode": "6291000000017"}, 409, 'Barcode 6291000000017 is already used by "Cola Can".'),
    ):
        r = _save(client, h, "products", record)
        assert r.status_code == code and text in r.json()["detail"], (record, r.text)

    # The same product again (an edit, keyed by its code) is fine; so is another one with
    # the same name but its own code (the sales picker keeps those apart on purpose).
    assert _save(client, h, "products", {"code": "ABC-1", "name": "Cola Can", "selling_price": 3,
                                         "barcode": "6291000000017", "min_stock": 5, "max_stock": 0}).status_code == 200
    assert _save(client, h, "products", {"code": "ABC-2", "name": "Cola Can"}).status_code == 200


def test_imports_and_lenient_saves_clean_and_carry_on(client, db, tenant):  # noqa: F811
    company_id, h = tenant
    assert _save(client, h, "products", {"code": "P-1", "name": "Rice", "barcode": "111"}).status_code == 200
    out = _bulk(client, h, "products", [
        {"code": "P-2", "name": "Sugar", "cost": "n/a", "price": -3},  # kept, bad numbers cleared
        {"code": "p-1", "name": "Rice 5kg"},                           # case variant of P-1: skipped
        {"code": "P-3", "name": ""},                                   # no name: skipped
        {"code": "P-4", "name": "Salt", "barcode": "111"},             # P-1's barcode: kept without it
    ])
    assert (out["saved"], out["skipped"], out["cleaned"]) == (2, 2, 2)
    # A product made from an AI-read purchase line (single save, lenient): never refused.
    assert _lenient(client, h, {"code": "P-5", "name": "Flour", "cost": "12..5"})["saved"] is True
    assert _lenient(client, h, {"code": "P-1", "name": "Rice"})["saved"] is True        # its own code: an update
    assert _lenient(client, h, {"code": "p-5", "name": "Flour"}) == {"ok": True, "saved": False, "skipped": True}
    db.expire_all()
    rows = {p.code: (p.name, p.cost, p.barcode) for p in db.query(CatalogProduct).filter(CatalogProduct.company_id == company_id)}
    # A cleared barcode stays on the record as "" (app/main_copy.py keeps a given field's key).
    assert rows == {"P-1": ("Rice", None, None), "P-2": ("Sugar", None, None), "P-4": ("Salt", None, ""),
                    "P-5": ("Flour", None, None)}
