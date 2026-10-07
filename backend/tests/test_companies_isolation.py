"""_resolve_company() (app/routers/companies.py) used to silently re-link a
user with an orphaned company_id to db.query(Company).first() — whichever
company happened to sort first in the table, an arbitrary unrelated tenant.
That auto-grants the (still is_admin=True) user full read/write access to a
stranger's company data the moment any bug ever nulls/breaks a company_id.
Fixed to fail closed: GET /companies/current now 404s instead of guessing,
and PUT /companies/current falls through to its existing "create a new
company for this user" branch instead of taking over an existing tenant."""
import pytest

from app.database import engine
from app.models import Company, User


@pytest.mark.skipif(engine.dialect.name != "sqlite",
                    reason="PostgreSQL's foreign key already makes an orphaned users.company_id impossible")
def test_orphaned_company_id_fails_closed_not_linked_to_another_tenant(client, db, auth_headers, second_tenant_headers):
    # second_tenant_headers's login already created a second real Company —
    # confirm it exists and note its name, so we can prove auth_headers's
    # user is never silently handed it.
    other_user = db.query(User).filter(User.email == "qa-other@taxflowqa.com").first()
    other_company_name = other_user.company.name

    admin_user = db.query(User).filter(User.email == "qa-admin@taxflowqa.com").first()
    admin_user.company_id = "does-not-exist-orphaned-id"
    db.add(admin_user)
    db.commit()

    r = client.get("/api/v1/companies/current", headers=auth_headers)
    assert r.status_code == 404, r.text

    db.refresh(admin_user)
    assert admin_user.company_id == "does-not-exist-orphaned-id", (
        "company_id must not have been silently rewritten to point at another tenant"
    )

    # A read never mutated anything; now exercise the write path too — it
    # must create a fresh company for this user, never adopt an existing one.
    before_company_count = db.query(Company).count()
    r2 = client.put("/api/v1/companies/current", headers=auth_headers, json={"name": "Recovered Co"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "Recovered Co"
    assert r2.json()["name"] != other_company_name

    after_company_count = db.query(Company).count()
    assert after_company_count == before_company_count + 1, "should create a brand-new company, not adopt an existing tenant's"

    db.refresh(admin_user)
    assert admin_user.company_id != "does-not-exist-orphaned-id"
    new_company = db.query(Company).filter(Company.id == admin_user.company_id).first()
    assert new_company is not None
    assert new_company.name == "Recovered Co"
