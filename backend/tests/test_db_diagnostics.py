"""Super Admin > System Health > database diagnostics. Its table list used to read n_live_tup
from pg_statio_user_tables (no such column) and failed with a 500 on PostgreSQL."""
from tests.test_impersonate_branch import _make_superadmin_headers


def test_db_diagnostics_answers(client, db, auth_headers):
    sa = _make_superadmin_headers(client, db)
    r = client.get("/api/v1/superadmin/db-diagnostics", headers=sa)
    assert r.status_code == 200, r.text
    body = r.json()
    if db.get_bind().dialect.name == "postgresql":
        assert body["supported"] is True
        assert body["top_tables"] and {"table", "size", "row_estimate"} <= set(body["top_tables"][0])
    else:
        assert body["supported"] is False


def test_db_diagnostics_is_superadmin_only(client, auth_headers):
    assert client.get("/api/v1/superadmin/db-diagnostics", headers=auth_headers).status_code == 403
