"""Super Admin sets how many branches a company may have (companies.max_branches); creating
a branch past it is refused with a clear message. No limit set: as before."""
from uuid import uuid4

from app.models import Company
from tests.test_company_stock_mode import _create_company
from tests.test_module_permissions import _make_superadmin


def _branch(client, headers, name):
    return client.post("/api/v1/branches", headers=headers, json={"name": name})


def test_limit_is_set_checked_raised_and_removed(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"bl-{tag}")
    company_id, admin = _create_company(client, sa, tag, max_branches=2)
    assert db.get(Company, company_id).max_branches == 2

    assert _branch(client, admin, f"Dubai {tag}").status_code == 201
    assert _branch(client, admin, f"Sharjah {tag}").status_code == 201
    third = _branch(client, admin, f"Ajman {tag}")
    assert third.status_code == 409
    assert third.json()["detail"] == ("Your plan allows 2 branches and you already have 2. "
                                      "Contact support to raise the limit.")

    # Super Admin sees branches used / limit, raises the limit, and the third branch goes through.
    detail = client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa).json()
    assert (detail["max_branches"], detail["branch_count"]) == (2, 2)
    assert client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa,
                        json={"name": f"Stock Mode Co {tag}", "max_branches": 3}).status_code == 200
    assert _branch(client, admin, f"Ajman {tag}").status_code == 201
    assert _branch(client, admin, f"Fujairah {tag}").status_code == 409

    # Null removes the limit; an edit that doesn't send it leaves the limit alone.
    assert client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa,
                        json={"name": f"Stock Mode Co {tag}", "max_branches": None}).status_code == 200
    assert _branch(client, admin, f"Fujairah {tag}").status_code == 201
    assert client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa,
                        json={"name": f"Stock Mode Co {tag}", "max_branches": 1}).status_code == 200
    assert client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa,
                        json={"name": f"Renamed {tag}"}).status_code == 200
    db.expire_all()
    assert db.get(Company, company_id).max_branches == 1


def test_company_admin_cannot_change_its_own_limit_and_no_limit_by_default(client, db):
    tag = uuid4().hex[:6]
    sa = _make_superadmin(client, db, f"bl2-{tag}")
    company_id, admin = _create_company(client, sa, tag)
    assert db.get(Company, company_id).max_branches is None
    for i in range(3):
        assert _branch(client, admin, f"B{i} {tag}").status_code == 201
    # The company's own settings endpoint ignores max_branches.
    r = client.put("/api/v1/companies/current", headers=admin, json={"name": f"Stock Mode Co {tag}", "max_branches": 1})
    assert r.status_code in (200, 422), r.text
    db.expire_all()
    assert db.get(Company, company_id).max_branches is None
    # A negative limit is rejected.
    r = client.patch(f"/api/v1/superadmin/companies/{company_id}", headers=sa, json={"name": "X", "max_branches": -1})
    assert r.status_code == 422
