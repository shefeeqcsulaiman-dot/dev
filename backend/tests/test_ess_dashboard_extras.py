"""GET /ess/me's new Dashboard-hero fields (company_name, shift_start/end)
and GET /ess/announcements -- both added alongside the ESS Dashboard
redesign (hero info chips + Latest Announcements card)."""
from app.models import Company, Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _ess_login(client, db, admin_headers, company_id, employee_no, username):
    emp = Employee(company_id=company_id, employee_no=employee_no, full_name=f"{employee_no} Staff",
                    basic_salary=5000, status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=admin_headers,
        json={"username": username, "password": f"{username}pw123", "is_active": True},
    )
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return emp, headers


def test_ess_me_reports_company_name(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSHERO-1", "esshero.co")

    company_name = client.get("/api/v1/auth/me", headers=auth_headers).json()["company"]["name"]
    r = client.get("/api/v1/ess/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["company_name"] == company_name


def test_ess_me_reports_company_currency(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSHERO-3", "esshero.cur")

    r = client.get("/api/v1/ess/me", headers=headers)
    assert r.status_code == 200, r.text
    expected = db.query(Company.currency).filter(Company.id == company_id).scalar()
    assert r.json()["currency"] == expected
    assert r.json()["currency"]  # never blank -- the payslip UI prefixes amounts with it


def test_ess_me_reports_shift_end_derived_from_start_plus_standard_hours(client, db, auth_headers):
    company_id = _company_id(client, auth_headers)
    _emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSHERO-2", "esshero.shift")

    r = client.post(
        "/api/v1/app-data", headers=auth_headers, params={"action": "save"},
        json={"collection": "hr_settings", "record": {"id": "late-rules-config", "startTime": "08:30", "graceMinutes": 0}},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/ess/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["shift_start"] == "08:30"
    assert r.json()["shift_end"] == "16:30"   # default 8h standard day


def test_ess_announcements_reads_posted_records_scoped_to_company(client, db, auth_headers, second_tenant_headers):
    company_id = _company_id(client, auth_headers)
    _emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSANN-1", "essann.reader")

    r = client.post(
        "/api/v1/app-data", headers=auth_headers, params={"action": "save"},
        json={"collection": "companyAnnouncements", "record": {
            "id": "ANN-1", "title": "New HR Policy", "message": "Please check the updated leave policy.", "date": "2026-09-05",
        }},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/ess/announcements", headers=headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["title"] == "New HR Policy"
    assert rows[0]["message"] == "Please check the updated leave policy."
    assert rows[0]["date"] == "2026-09-05"

    # A different company's admin posting to their own tenant must not
    # leak into this company's employee feed.
    r2 = client.post(
        "/api/v1/app-data", headers=second_tenant_headers, params={"action": "save"},
        json={"collection": "companyAnnouncements", "record": {"id": "ANN-OTHER", "title": "Other Tenant Notice"}},
    )
    assert r2.status_code == 200, r2.text
    r3 = client.get("/api/v1/ess/announcements", headers=headers)
    assert r3.status_code == 200, r3.text
    assert [row["title"] for row in r3.json()] == ["New HR Policy"]


def test_ess_announcements_skips_records_with_no_title(client, db, auth_headers):
    # auth_headers reuses one shared test company all session, and an
    # earlier test in this file may already have posted a real
    # announcement into it -- assert the no-title record specifically
    # never appears, rather than asserting the whole feed is empty.
    company_id = _company_id(client, auth_headers)
    _emp, headers = _ess_login(client, db, auth_headers, company_id, "ESSANN-2", "essann.notitle")

    r = client.post(
        "/api/v1/app-data", headers=auth_headers, params={"action": "save"},
        json={"collection": "companyAnnouncements", "record": {"id": "ANN-BLANK", "message": "No title here"}},
    )
    assert r.status_code == 200, r.text

    r = client.get("/api/v1/ess/announcements", headers=headers)
    assert r.status_code == 200, r.text
    assert "ANN-BLANK" not in [row["id"] for row in r.json()]
    assert "No title here" not in [row["message"] for row in r.json()]
