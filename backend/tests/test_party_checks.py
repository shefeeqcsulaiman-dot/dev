"""Customers and suppliers are checked before they're saved (app/party_checks.py): a form
save is refused with a clear message, a bulk import cleans the bad field and carries on."""
from uuid import uuid4

import pytest

from app.models import Party
from tests.conftest import ensure_user

TRN_A, TRN_B = "100123456700003", "100987654300003"


@pytest.fixture()
def tenant(client, db):
    tag = uuid4().hex[:8]
    email = f"pc-{tag}@taxflowqa.com"
    user = ensure_user(db, email, f"94{int(tag, 16) % 10**13:013d}")
    db.commit()
    token = client.post("/api/v1/auth/login", json={"email": email, "password": "admin123"}).json()["access_token"]
    return user.company_id, {"Authorization": f"Bearer {token}"}


def _save(client, headers, collection, record):
    return client.post("/api/v1/app-data?action=save", headers=headers, json={"collection": collection, "record": record})


def _bulk(client, headers, collection, records):
    r = client.post("/api/v1/app-data?action=bulk-save", headers=headers, json={"collection": collection, "records": records})
    assert r.status_code == 200, r.text
    return r.json()


def test_form_saves_are_refused_with_a_reason(client, db, tenant):
    _, h = tenant
    assert _save(client, h, "customers", {"name": "Acme LLC", "trn": "100-1234-5670-0003", "email": " a@acme.ae "}).status_code == 200
    party = db.query(Party).filter(Party.name == "Acme LLC").one()
    assert (party.trn, party.email) == (TRN_A, "a@acme.ae")      # TRN digits only, email trimmed

    for record, code, text in (
        ({"name": "  ", "trn": ""}, 422, "Name is required."),
        ({"name": "Beta", "trn": "12345"}, 422, "TRN must be 15 digits."),
        ({"name": "Beta", "email": "beta-at-mail"}, 422, "is not an email address"),
        ({"name": "ACME llc"}, 409, 'A customer named "Acme LLC" already exists.'),
        ({"name": "Beta", "trn": TRN_A}, 409, f'TRN {TRN_A} already belongs to customer "Acme LLC".'),
    ):
        r = _save(client, h, "customers", record)
        assert r.status_code == code and text in r.json()["detail"], (record, r.text)

    # Saving the same customer again (an edit) is fine, TRN included; so is a new one.
    assert _save(client, h, "customers", {"name": "Acme LLC", "trn": TRN_A, "phone": "04 123"}).status_code == 200
    assert _save(client, h, "customers", {"name": "Beta", "trn": TRN_B}).status_code == 200
    # A supplier may share a customer's name and TRN (the same company buying and selling).
    assert _save(client, h, "vendors", {"name": "Acme LLC", "trn": TRN_A, "category": "Goods"}).status_code == 200
    r = _save(client, h, "vendors", {"name": "Gamma", "trn": TRN_A, "category": "Goods"})
    assert r.status_code == 409 and 'supplier "Acme LLC"' in r.json()["detail"]


def test_bulk_imports_clean_bad_fields_and_skip_duplicates(client, db, tenant):
    company_id, h = tenant
    assert _save(client, h, "vendors", {"name": "Gulf Bev", "trn": TRN_A, "category": "Goods"}).status_code == 200
    out = _bulk(client, h, "vendors", [
        {"name": "Delta Paper", "trn": "1001", "email": "bad"},     # kept, TRN and email dropped
        {"name": "GULF BEV", "trn": TRN_B},                         # case variant of an existing one: skipped
        {"name": "", "trn": TRN_B},                                 # no name: skipped
        {"name": "Echo Foods", "trn": TRN_A},                       # another supplier's TRN: kept without it
        {"name": "Fox Trading", "trn": TRN_B, "email": "f@fox.ae"},  # fine
        {"name": "fox trading", "trn": TRN_B},                      # same name twice in one batch: skipped
    ])
    assert (out["saved"], out["skipped"], out["cleaned"]) == (3, 3, 2)
    rows = {p.name: (p.trn, p.email) for p in db.query(Party).filter(Party.company_id == company_id, Party.kind == "vendor")}
    assert rows == {"Gulf Bev": (TRN_A, None), "Delta Paper": (None, None), "Echo Foods": (None, None),
                    "Fox Trading": (TRN_B, "f@fox.ae")}


def test_other_collections_are_untouched(client, tenant):
    _, h = tenant
    assert _save(client, h, "salesCategories", {"name": "", "trn": "1", "email": "x"}).status_code == 200


def test_padded_names_keep_their_key(client, db, tenant):
    """The name is the record's key: it's checked trimmed but stored as sent, so re-saving
    or deleting with the same text still finds the same record."""
    company_id, h = tenant
    rec = {"name": "  Padded Co ", "trn": TRN_B}
    assert _save(client, h, "customers", rec).status_code == 200
    assert _save(client, h, "customers", {**rec, "phone": "050"}).status_code == 200   # not a clash with itself
    r = client.post("/api/v1/app-data?action=delete", headers=h, json={"collection": "customers", "record": rec})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(Party).filter(Party.company_id == company_id, Party.name == "Padded Co").count() == 0
