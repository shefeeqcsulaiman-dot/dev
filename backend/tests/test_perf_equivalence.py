"""Speed-ups must not change results. These pin the batched code paths to the
one-at-a-time code they replaced, and check the query count really dropped."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import event

from app.database import engine
from app.models import Employee, LeaveRequest
from app.routers.hr_access import _role_permission_keys_bulk
from app.auth_principal import _role_permission_keys
from app.routers.leave import _used_days_by_employee_and_type, _used_days_for_type


def _company_id(client, headers):
    return client.get("/api/v1/auth/me", headers=headers).json()["company"]["id"]


class _QueryCounter:
    def __enter__(self):
        self.n = 0
        self._fn = lambda *a, **k: setattr(self, "n", self.n + 1)
        event.listen(engine, "before_cursor_execute", self._fn)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._fn)


def _ess_login(client, db, admin_headers, cid, no, username):
    emp = Employee(company_id=cid, employee_no=no, full_name=f"{no} Staff", department="Ops", status="active")
    db.add(emp)
    db.commit()
    db.refresh(emp)
    r = client.put(f"/api/v1/hr/admin/employees/{emp.id}/portal-access", headers=admin_headers,
                   json={"username": username, "password": f"{username}pw123", "is_active": True})
    assert r.status_code == 200, r.text
    login = client.post("/api/v1/ess/login", json={"username": username, "password": f"{username}pw123"})
    return emp, {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_grouped_used_days_matches_the_per_employee_per_type_lookup(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    year = datetime.now(UTC).year
    emps = []
    for i in range(4):
        e = Employee(company_id=cid, employee_no=f"PERF-L{i}", full_name=f"Perf Leave {i}", department="Ops", status="active")
        db.add(e)
        emps.append(e)
    db.flush()
    rows = [
        (0, "Annual Leave", f"{year}-03-01", 3, "approved"), (0, "Annual Leave", f"{year}-06-10", 2, "approved"),
        (0, "Sick Leave", f"{year}-04-01", 4, "approved"), (1, "Annual Leave", f"{year}-05-01", 5, "approved"),
        (1, "Annual Leave", f"{year}-05-20", 9, "pending"),            # pending: not counted
        (1, "Annual Leave", f"{year - 1}-12-20", 7, "approved"),        # last year: not counted
        (2, "Casual Leave", f"{year}-01-01", 1, "approved"), (2, "Annual Leave", f"{year + 1}-01-01", 6, "approved"),  # next year: not counted
        (2, "Unpaid Leave", f"{year}-12-31", 2, "approved"),
    ]
    for idx, leave_type, start, days, status in rows:
        db.add(LeaveRequest(company_id=cid, employee_id=emps[idx].id, leave_type=leave_type, start_date=start, end_date=start, days=days, status=status))
    db.commit()

    grouped = _used_days_by_employee_and_type(db, cid)
    types = ["Annual Leave", "Sick Leave", "Casual Leave", "Unpaid Leave", "Emergency Leave", "Hajj Leave"]
    for e in emps:
        for t in types:
            assert grouped.get((e.id, t), 0) == _used_days_for_type(db, cid, e.id, t), (e.employee_no, t)
        single = _used_days_by_employee_and_type(db, cid, e.id)
        assert {k: v for k, v in grouped.items() if k[0] == e.id} == single


def test_leave_balance_endpoints_give_the_same_answer_with_far_fewer_queries(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    year = datetime.now(UTC).year
    for i in range(12):
        e = Employee(company_id=cid, employee_no=f"PERF-B{i}", full_name=f"Perf Bal {i}", department="Ops", status="active")
        db.add(e)
        db.flush()
        db.add(LeaveRequest(company_id=cid, employee_id=e.id, leave_type="Annual Leave", start_date=f"{year}-02-0{1 + i % 8}",
                            end_date=f"{year}-02-0{1 + i % 8}", days=1 + i % 3, status="approved"))
        db.add(LeaveRequest(company_id=cid, employee_id=e.id, leave_type="Sick Leave", start_date=f"{year}-03-01", end_date=f"{year}-03-01", days=2, status="approved"))
    db.commit()
    with _QueryCounter() as q:
        r = client.get("/api/v1/leave/balance", headers=auth_headers)
    assert r.status_code == 200, r.text
    rows = {x["employee_id"]: x for x in r.json()}
    mine = db.query(Employee).filter(Employee.employee_no.like("PERF-B%")).all()
    assert len(mine) == 12
    for e in mine:
        row = rows[e.id]
        assert row["used"] == _used_days_for_type(db, cid, e.id, "Annual Leave")
        for t, info in row["by_type"].items():
            assert info["used"] == _used_days_for_type(db, cid, e.id, t), (e.employee_no, t)
            assert info["remaining"] == max(0, info["entitlement"] - info["used"])
    assert q.n < 40, f"leave/balance made {q.n} queries for {len(rows)} employees"     # was ~ employees x leave types


def test_ess_leave_balance_unchanged_and_batched(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, headers = _ess_login(client, db, auth_headers, cid, "PERF-E1", "perf.e1")
    year = datetime.now(UTC).year
    for leave_type, days in (("Annual Leave", 4), ("Sick Leave", 3), ("Casual Leave", 1)):
        db.add(LeaveRequest(company_id=cid, employee_id=emp.id, leave_type=leave_type, start_date=f"{year}-04-10", end_date=f"{year}-04-10", days=days, status="approved"))
    db.commit()
    r = client.get("/api/v1/ess/leave-balance", headers=headers)
    assert r.status_code == 200, r.text
    by_type = r.json()["by_type"]
    for t, info in by_type.items():
        assert info["used"] == _used_days_for_type(db, cid, emp.id, t), t
    assert by_type["Annual Leave"]["used"] == 4 and by_type["Sick Leave"]["used"] == 3


def test_role_lists_match_the_per_role_permission_lookup(client, db, auth_headers):
    from app.models import Role
    cid = _company_id(client, auth_headers)
    made = client.post("/api/v1/hr/admin/roles", headers=auth_headers, json={
        "role_name": "Perf Role", "description": "t", "permission_keys": ["leave:view", "leave:edit", "hr_workflow:view"], "department_scope": ["@own"]})
    assert made.status_code == 201, made.text
    listed = client.get("/api/v1/hr/admin/roles", headers=auth_headers).json()
    assert len(listed) >= 6
    roles = db.query(Role).filter(Role.company_id == cid).all()
    bulk = _role_permission_keys_bulk(db, roles)
    for role in roles:
        assert bulk[role.id] == _role_permission_keys(db, role), role.role_name
    by_name = {r["role_name"]: r for r in listed}
    for role in roles:
        assert by_name[role.role_name]["permissions"] == sorted(_role_permission_keys(db, role))
    assert by_name["Perf Role"]["permissions"] == ["hr_workflow:view", "leave:edit", "leave:view"]
    assert by_name["Perf Role"]["department_scope"] == ["@own"]
    assert "leave:view" not in by_name["Employee"]["permissions"]                       # default roles keep their own grants
    with _QueryCounter() as q:
        client.get("/api/v1/hr/admin/roles", headers=auth_headers)
    assert q.n < 12, f"roles list made {q.n} queries"


def test_last_activity_is_still_recorded_but_not_written_on_every_request(client, db, auth_headers):
    cid = _company_id(client, auth_headers)
    emp, headers = _ess_login(client, db, auth_headers, cid, "PERF-A1", "perf.a1")
    db.refresh(emp)
    emp.last_activity = None
    db.commit()
    assert client.get("/api/v1/hr/me", headers=headers).status_code == 200
    db.refresh(emp)
    first = emp.last_activity
    assert first is not None                                                # first request records activity
    assert client.get("/api/v1/hr/me", headers=headers).status_code == 200
    db.refresh(emp)
    assert emp.last_activity == first                                       # a request right after does not rewrite it
    emp.last_activity = datetime.now(UTC) - timedelta(minutes=5)
    stale = emp.last_activity
    db.commit()
    assert client.get("/api/v1/hr/me", headers=headers).status_code == 200
    db.refresh(emp)
    assert emp.last_activity.replace(tzinfo=None) > stale.replace(tzinfo=None)  # after a minute of quiet it refreshes
