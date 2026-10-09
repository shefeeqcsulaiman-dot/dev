"""Stock mappings with no cost get one from products / purchase lines. The lookup reads
only purchases whose lines name a missing item (found through document_lines) instead
of decoding the whole purchase history, and gives the same costs for those items."""
import json
from decimal import Decimal
from uuid import uuid4

from app.models import AppDataRecord, StockProductMapping
from app.routers.inventory import normalize_key, purchase_cost_lookup
from tests.test_document_lines import tenant  # noqa: F401  (fixture)


def _add(db, company_id, collection, payload):
    db.add(AppDataRecord(company_id=company_id, collection=collection, record_key=payload["id"], payload=json.dumps(payload)))
    db.commit()


def test_targeted_lookup_matches_full_scan_for_wanted_keys(db, tenant):  # noqa: F811
    cid, _ = tenant
    tag = uuid4().hex[:6]
    _add(db, cid, "products", {"id": f"P-{tag}", "code": f"PRD-{tag}", "name": f"Catalog {tag}", "cost": "4.50"})
    _add(db, cid, "purchaseRecords", {"id": f"PU1-{tag}", "lines": [
        {"sku": f"SKU-{tag}", "product": f" Widget {tag} ", "unit_cost": "12.25"},
        {"sku": f"OTHER-{tag}", "description": f"Other {tag}", "unit_cost": "99"},
    ]})
    _add(db, cid, "purchaseRecords", {"id": f"PU2-{tag}", "lines": [
        {"description": f"Gadget {tag}", "unit_cost_before_discount": "7"},
        "not a line",
    ]})
    _add(db, cid, "purchaseRecords", {"id": f"PU3-{tag}", "lines": [{"sku": f"UNRELATED-{tag}", "unit_cost": "1"}]})

    full = purchase_cost_lookup(db, cid)
    wanted = {normalize_key(k) for k in (f"sku-{tag}", f"Widget {tag}", f"GADGET {tag}", f"prd-{tag}", f"missing-{tag}")}
    targeted = purchase_cost_lookup(db, cid, wanted)
    assert {k: targeted.get(k) for k in wanted} == {k: full.get(k) for k in wanted}
    assert targeted[normalize_key(f"SKU-{tag}")] == Decimal("12.25")
    assert targeted[normalize_key(f"Gadget {tag}")] == Decimal("7.00")
    assert targeted[normalize_key(f"PRD-{tag}")] == Decimal("4.50")
    # Unrelated purchases were not read.
    assert normalize_key(f"UNRELATED-{tag}") not in targeted
    assert purchase_cost_lookup(db, cid, set()) == {}


def test_mappings_endpoint_fills_missing_cost(client, db, tenant):  # noqa: F811
    cid, headers = tenant
    tag = uuid4().hex[:6]
    _add(db, cid, "purchaseRecords", {"id": f"PU-{tag}", "lines": [{"sku": f"MAP-{tag}", "unit_cost": "3.75"}]})
    db.add(StockProductMapping(company_id=cid, sku=f"map-{tag}", name=f"Mapped {tag}", cost=0))
    db.commit()
    rows = client.get("/api/v1/inventory/mappings", headers=headers).json()
    mine = next(r for r in rows if r["sku"] == f"map-{tag}")
    assert Decimal(str(mine["cost"])) == Decimal("3.75")
