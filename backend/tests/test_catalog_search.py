"""GET /app-data/catalog/{products|customers|vendors}: what the product, customer and
supplier pickers search as you type. Bootstrap only carries the newest 500 products, so
anything older must be findable here."""
import json
from uuid import uuid4

from app.models import AppDataRecord, User
from tests.test_module_permissions import _make_restricted_company


def _company_id(db):
    return db.query(User.company_id).filter(User.email == "qa-admin@taxflowqa.com").scalar()


def _search(client, headers, collection, q, **params):
    return client.get(f"/api/v1/app-data/catalog/{collection}", headers=headers, params={"q": q, **params})


def test_finds_products_bootstrap_leaves_out(client, db, auth_headers):
    cid = _company_id(db)
    tag = uuid4().hex[:6].upper()
    db.add_all([
        AppDataRecord(company_id=cid, collection="products", record_key=f"OLD-{tag}-{i:03d}",
                      payload=json.dumps({"code": f"OLD-{tag}-{i:03d}", "name": f"Old Widget {tag} {i}", "unit": "PCS", "selling_price": 10 + i}))
        for i in range(520)
    ])
    db.commit()
    boot = client.get("/api/v1/app-data", headers=auth_headers).json()
    loaded = {p.get("code") for p in boot.get("products", [])}
    assert f"OLD-{tag}-000" not in loaded  # the oldest are beyond bootstrap's 500
    r = _search(client, auth_headers, "products", f"old-{tag}-000")
    assert r.status_code == 200, r.text
    records = r.json()["records"]
    assert [p["code"] for p in records] == [f"OLD-{tag}-000"]
    assert records[0]["selling_price"] == 10


def test_matches_name_and_ranks_key_prefix_first(client, db, auth_headers):
    cid = _company_id(db)
    tag = uuid4().hex[:6]
    db.add_all([
        AppDataRecord(company_id=cid, collection="customers", record_key=f"Zeta Trading {tag}",
                      payload=json.dumps({"name": f"Zeta Trading {tag}", "trn": "100200300400500", "email": f"ops-{tag}@alpha.example"})),
        AppDataRecord(company_id=cid, collection="customers", record_key=f"Alpha {tag} LLC",
                      payload=json.dumps({"name": f"Alpha {tag} LLC"})),
    ])
    db.commit()
    names = [c["name"] for c in _search(client, auth_headers, "customers", "alpha").json()["records"] if tag in c["name"]]
    assert names == [f"Alpha {tag} LLC", f"Zeta Trading {tag}"]  # key prefix first, then a match in the email
    by_trn = _search(client, auth_headers, "customers", "100200300400500").json()["records"]
    assert f"Zeta Trading {tag}" in [c["name"] for c in by_trn]


def test_wildcards_are_literal_and_limit_applies(client, db, auth_headers):
    cid = _company_id(db)
    tag = uuid4().hex[:6]
    db.add_all([
        AppDataRecord(company_id=cid, collection="vendors", record_key=f"V_{tag}", payload=json.dumps({"name": f"V_{tag}"})),
        AppDataRecord(company_id=cid, collection="vendors", record_key=f"VX{tag}", payload=json.dumps({"name": f"VX{tag}"})),
    ])
    db.commit()
    assert [v["name"] for v in _search(client, auth_headers, "vendors", f"v_{tag}").json()["records"]] == [f"V_{tag}"]
    assert len(_search(client, auth_headers, "vendors", "", limit=1).json()["records"]) == 1


def test_access_rules(client, db, auth_headers):
    assert _search(client, auth_headers, "employees", "a").status_code == 404
    cid = _company_id(db)
    tag = uuid4().hex[:6]
    db.add(AppDataRecord(company_id=cid, collection="products", record_key=f"PRIV-{tag}", payload=json.dumps({"code": f"PRIV-{tag}", "name": "Private"})))
    db.commit()
    _, other, _ = _make_restricted_company(client, db, f"cat-{uuid4().hex[:4]}", ["sales"])
    assert _search(client, other, "products", f"priv-{tag}").json()["records"] == []  # never another company's
    # Same answer as GET /records/{collection} for the same login, whatever its modules.
    _, hr_only, _ = _make_restricted_company(client, db, f"cat-{uuid4().hex[:4]}", ["hr"])
    for collection in ("products", "customers", "vendors"):
        assert (_search(client, hr_only, collection, "a").status_code
                == client.get(f"/api/v1/app-data/records/{collection}", headers=hr_only).status_code)
