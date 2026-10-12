"""parties is the main copy of customers and suppliers (step C): a row holds the whole
record, saves store the JSON its row regenerates, and the tools find and fix records that
were saved another way."""
import json

from app import parties
from app.models import AppDataRecord, Party
from app.parties import canonical_party, party_fields, party_record
from tests.test_party_checks import _bulk, _save, tenant  # noqa: F401  (fixture)


def test_round_trip_keeps_every_field_and_is_stable():
    doc = {"id": "C-1", "name": "  Padded Co ", "trn": "100-123-456-700-003", "email": " a@b.ae ", "mobile": "050 1",
           "contact_person": "Ali", "credit_limit": "5,000", "status": "", "notes": {"vip": True}, "source": "CSV",
           "emirate": None, "_v": 3}
    out = canonical_party(doc)
    assert out == {"name": "  Padded Co ", "trn": "100123456700003", "email": "a@b.ae", "phone": "050 1",
                   "contact": "Ali", "credit_limit": 5000.0, "status": "", "emirate": "", "id": "C-1", "mobile": "050 1",
                   "contact_person": "Ali", "notes": {"vip": True}, "source": "CSV", "_v": 3}
    assert canonical_party(out) == out                                   # stable
    fields = party_fields(doc)
    assert (fields["name"], fields["phone"], fields["credit_limit"]) == ("Padded Co", "050 1", 5000)
    assert party_record(fields) == out                                   # the row alone rebuilds it
    assert canonical_party({"name": "X", "credit_limit": "n/a"}) == {"name": "X"}   # not a number: dropped


def test_saves_store_what_the_row_regenerates(client, db, tenant):  # noqa: F811
    company_id, h = tenant
    assert _save(client, h, "customers", {"name": "Gulf Co", "trn": "100 123 456 700 003", "credit_limit": "2,500",
                                          "website": "gulf.ae", "phone": ""}).status_code == 200
    _bulk(client, h, "vendors", [{"name": "Delta", "category": "Goods", "source": "AI Purchase Upload", "trn": ""}])
    db.expire_all()
    for name in ("Gulf Co", "Delta"):
        row = db.query(Party).filter(Party.company_id == company_id, Party.name == name).one()
        stored = json.loads(db.get(AppDataRecord, row.id).payload)
        assert party_record(row) == stored, name
    gulf = json.loads(db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id,
                                                     AppDataRecord.record_key == "Gulf Co").one().payload)
    assert (gulf["trn"], gulf["credit_limit"], gulf["phone"], gulf["website"]) == ("100123456700003", 2500.0, "", "gulf.ae")
    assert parties.verify_company(db, company_id) and parties.stale_json(db, company_id) == []

    # A record written around the save path (e.g. a restore) is found and rewritten.
    rec = db.query(AppDataRecord).filter(AppDataRecord.company_id == company_id, AppDataRecord.record_key == "Delta").one()
    rec.payload = json.dumps({"name": "Delta", "category": "Goods", "trn": "1-2", "credit_limit": "9,000"})
    db.commit()
    assert parties.stale_json(db, company_id) == [rec.id] and parties.verify_company(db, company_id)
    parties.rewrite_json(db, [rec.id])
    db.commit()
    assert json.loads(db.get(AppDataRecord, rec.id).payload) == {"name": "Delta", "trn": "12", "category": "Goods",
                                                                 "credit_limit": 9000.0}
    assert parties.stale_json(db, company_id) == [] and parties.verify_company(db, company_id)
