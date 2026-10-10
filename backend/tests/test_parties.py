"""Customers and vendors are mirrored into the parties table on every kind of write
(step A of moving them out of JSON); the JSON stays the source of truth."""
from uuid import uuid4

from app import parties
from app.models import AppDataRecord, Party
from tests.test_document_lines import tenant  # noqa: F401  (fixture)


def _save(client, headers, collection, record):
    r = client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": collection, "record": record})
    assert r.status_code == 200, r.text


def test_save_edit_delete_keep_parties_current(client, db, tenant):  # noqa: F811
    cid, headers = tenant
    tag = uuid4().hex[:6]
    cust = {"id": f"C-{tag}", "name": f"  Gulf Traders {tag} ", "trn": "100-234-567-890-003", "email": "a@b.ae",
            "credit_limit": "5,000", "emirate": "Dubai"}
    _save(client, headers, "customers", cust)
    db.expire_all()
    row = db.query(Party).filter(Party.company_id == cid, Party.kind == "customer", Party.name_key == f"gulf traders {tag}").one()
    assert row.trn == "100234567890003" and str(row.credit_limit) == "5000.00" and row.emirate == "Dubai"

    # Same record (customers are keyed by name): the row is updated, not duplicated.
    _save(client, headers, "customers", {**cust, "credit_limit": "n/a", "phone": "+971 4 000"})
    db.expire_all()
    rows = db.query(Party).filter(Party.company_id == cid, Party.kind == "customer", Party.name.like(f"%{tag}%")).all()
    assert len(rows) == 1 and rows[0].credit_limit is None and rows[0].phone == "+971 4 000"

    _save(client, headers, "vendors", {"id": f"V-{tag}", "name": f"Supplier {tag}", "trn": "", "category": "Goods"})
    db.expire_all()
    assert db.query(Party).filter(Party.company_id == cid, Party.kind == "vendor", Party.name == f"Supplier {tag}").one().trn is None

    r = client.post("/api/v1/app-data?action=delete", headers=headers, json={"collection": "customers", "record": cust})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(Party).filter(Party.company_id == cid, Party.kind == "customer", Party.name.like(f"%{tag}%")).count() == 0
    assert parties.verify_company(db, cid)


def test_bulk_writes_and_rebuild(client, db, tenant):  # noqa: F811
    cid, headers = tenant
    tag = uuid4().hex[:6]
    recs = [{"id": f"B{i}-{tag}", "name": f"Bulk {i} {tag}"} for i in range(5)]
    r = client.post("/api/v1/app-data?action=bulk-save", headers=headers, json={"collection": "customers", "records": recs})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(Party).filter(Party.company_id == cid, Party.name.like(f"Bulk % {tag}")).count() == 5

    # Bulk delete through the ORM query API removes the rows too.
    db.query(AppDataRecord).filter(AppDataRecord.company_id == cid, AppDataRecord.collection == "customers",
                                   AppDataRecord.payload.like(f'%Bulk 0 {tag}%')).delete(synchronize_session=False)
    db.commit()
    assert db.query(Party).filter(Party.company_id == cid, Party.name == f"Bulk 0 {tag}").count() == 0
    assert parties.verify_company(db, cid)

    db.query(Party).filter(Party.company_id == cid).delete()
    db.commit()
    assert not parties.verify_company(db, cid)
    parties.mirror.rebuild_company(db, cid)
    db.commit()
    assert parties.verify_company(db, cid)


def test_bad_payload_is_skipped():
    assert parties.party_rows(("r", "c", "customers", "not json", None, None)) == []
    assert parties.party_rows(("r", "c", "vendors", "[1,2]", None, None)) == []
