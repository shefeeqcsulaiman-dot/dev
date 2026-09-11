"""Regression coverage for the employee photo pipeline.

HRMS's Edit Employee form saves the whole employee record (including a
`photo` field, a compressed base64 data URL) through the generic
/app-data?action=save "employees" collection bridge. That JSON blob was
never mirrored onto the real Employee SQL row, so anything reading from
the SQL table -- ESS's /ess/me in particular -- had no way to show it.
sync_domain_model() now copies `record["photo"]` onto Employee.photo, the
same way it already does for iban, and /ess/me returns it."""
from app.models import Employee


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


def _save_employee_record(client, headers, **fields):
    record = {
        "id": fields.pop("id"),
        "name": fields.pop("name"),
        **fields,
    }
    return client.post(
        "/api/v1/app-data?action=save",
        headers=headers,
        json={"collection": "employees", "record": record},
    )


def test_saving_employee_photo_mirrors_onto_employee_row(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    photo = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/"

    r = _save_employee_record(client, auth_headers, id="PHOTO-001", name="Photo Test", photo=photo)
    assert r.status_code == 200, r.text

    emp = db.query(Employee).filter(
        Employee.company_id == company_id, Employee.employee_no == "PHOTO-001",
    ).one()
    assert emp.photo == photo


def test_saving_employee_without_photo_does_not_clear_existing_one(client, auth_headers, db):
    """sync_domain_model only sets emp.photo when the incoming record
    actually carries one (same "only if truthy" convention as iban) -- a
    partial/legacy write elsewhere in the collection must not wipe out a
    photo that was already mirrored in."""
    company_id = _company_id(client, auth_headers)
    photo = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/"
    r1 = _save_employee_record(client, auth_headers, id="PHOTO-002", name="Photo Keep Test", photo=photo)
    assert r1.status_code == 200, r1.text

    r2 = _save_employee_record(client, auth_headers, id="PHOTO-002", name="Photo Keep Test", department="Finance")
    assert r2.status_code == 200, r2.text

    emp = db.query(Employee).filter(
        Employee.company_id == company_id, Employee.employee_no == "PHOTO-002",
    ).one()
    assert emp.photo == photo
    assert emp.department == "Finance"


def test_ess_me_returns_employee_photo(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    photo = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/"
    r = _save_employee_record(client, auth_headers, id="PHOTO-003", name="Photo ESS Test", photo=photo)
    assert r.status_code == 200, r.text

    emp = db.query(Employee).filter(
        Employee.company_id == company_id, Employee.employee_no == "PHOTO-003",
    ).one()
    grant = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=auth_headers,
        json={"username": "ess.photo.test", "password": "essphoto123", "is_active": True},
    )
    assert grant.status_code == 200, grant.text

    login = client.post("/api/v1/ess/login", json={"username": "ess.photo.test", "password": "essphoto123"})
    assert login.status_code == 200, login.text
    ess_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/api/v1/ess/me", headers=ess_headers)
    assert me.status_code == 200, me.text
    assert me.json()["photo"] == photo


def test_ess_me_photo_is_null_when_none_saved(client, auth_headers, db):
    company_id = _company_id(client, auth_headers)
    r = _save_employee_record(client, auth_headers, id="PHOTO-004", name="No Photo Test")
    assert r.status_code == 200, r.text

    emp = db.query(Employee).filter(
        Employee.company_id == company_id, Employee.employee_no == "PHOTO-004",
    ).one()
    grant = client.put(
        f"/api/v1/hr/admin/employees/{emp.id}/portal-access",
        headers=auth_headers,
        json={"username": "ess.nophoto.test", "password": "essnophoto123", "is_active": True},
    )
    assert grant.status_code == 200, grant.text

    login = client.post("/api/v1/ess/login", json={"username": "ess.nophoto.test", "password": "essnophoto123"})
    assert login.status_code == 200, login.text
    ess_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    me = client.get("/api/v1/ess/me", headers=ess_headers)
    assert me.status_code == 200, me.text
    assert me.json()["photo"] is None
