"""Alembic adoption (app/migrate.py): fresh databases, pre-Alembic databases,
and repeat runs all end at the head revision with the full schema."""
import pathlib
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.migrate import BASELINE_REVISION, head_revision, run_migrations


@pytest.fixture()
def scratch_engine(tmp_path: pathlib.Path):
    eng = create_engine(f"sqlite:///{tmp_path / f'mig-{uuid.uuid4().hex}.db'}")
    yield eng
    eng.dispose()


def _revision(eng) -> str | None:
    with eng.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def test_empty_database_is_created_and_stamped_at_head(scratch_engine):
    run_migrations(scratch_engine)
    tables = set(inspect(scratch_engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert _revision(scratch_engine) == head_revision()


def test_pre_alembic_database_gets_legacy_patches_then_head(scratch_engine):
    # A database built the old way: create_all, no alembic_version table, and
    # missing a column that only the legacy startup patches add back.
    Base.metadata.create_all(bind=scratch_engine)
    with scratch_engine.begin() as conn:
        conn.execute(text("ALTER TABLE trial_requests DROP COLUMN employee_count"))
    assert "employee_count" not in {c["name"] for c in inspect(scratch_engine).get_columns("trial_requests")}

    run_migrations(scratch_engine)

    assert "employee_count" in {c["name"] for c in inspect(scratch_engine).get_columns("trial_requests")}
    assert "schema_flags" in inspect(scratch_engine).get_table_names()
    assert _revision(scratch_engine) == head_revision()


def test_rerun_is_a_no_op(scratch_engine):
    run_migrations(scratch_engine)
    run_migrations(scratch_engine)
    assert _revision(scratch_engine) == head_revision()


def test_baseline_is_the_root_revision():
    from alembic.script import ScriptDirectory
    from app.migrate import alembic_config

    script = ScriptDirectory.from_config(alembic_config())
    assert script.get_base() == BASELINE_REVISION


def test_direct_url_is_used_for_migrations_when_set(tmp_path, monkeypatch):
    # Behind PgBouncer, migrations must bypass the pooler (DATABASE_DIRECT_URL).
    from app.config import get_settings

    direct = tmp_path / "direct.db"
    monkeypatch.setattr(get_settings(), "database_direct_url", f"sqlite:///{direct}")
    run_migrations()
    eng = create_engine(f"sqlite:///{direct}")
    try:
        assert _revision(eng) == head_revision()
    finally:
        eng.dispose()


def test_revision_ids_fit_alembic_version_column():
    """alembic_version.version_num is VARCHAR(32): PostgreSQL refuses a longer id (SQLite
    doesn't check, which is how a 35-character 0012 got through), so every startup on a
    PostgreSQL database would fail to record it."""
    from alembic.script import ScriptDirectory
    from app.migrate import alembic_config

    too_long = [rev.revision for rev in ScriptDirectory.from_config(alembic_config()).walk_revisions() if len(rev.revision) > 32]
    assert too_long == []


def test_database_recorded_under_a_renamed_revision_still_upgrades(scratch_engine):
    from app.migrate import _RENAMED_REVISIONS

    run_migrations(scratch_engine)
    old, new = next(iter(_RENAMED_REVISIONS.items()))
    with scratch_engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :old"), {"old": old})
    run_migrations(scratch_engine)
    assert _revision(scratch_engine) == head_revision()


def test_migrations_on_postgresql():
    """The PostgreSQL CI job builds tables from the models (conftest), so it never ran the
    migrations themselves; PostgreSQL-only failures (a too-long revision id, a statement
    SQLite accepts) went unseen. With TEST_DATABASE_URL set, migrate a scratch database:
    empty -> head, then a second run that must change nothing."""
    import os

    from sqlalchemy.engine import make_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("needs TEST_DATABASE_URL (PostgreSQL)")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = f"mig_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    scratch = create_engine(make_url(url).set(database=name))
    try:
        run_migrations(scratch)
        run_migrations(scratch)
        assert _revision(scratch) == head_revision()
        assert set(Base.metadata.tables) <= set(inspect(scratch).get_table_names())
    finally:
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_startup_lock_wait_is_bounded_on_postgres(monkeypatch):
    """A session that never releases the startup/migration lock must not hang a new
    instance past its health check (DO deploys failed this way, 2026-10-10)."""
    import os

    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        pytest.skip("needs TEST_DATABASE_URL (PostgreSQL)")
    import time

    from sqlalchemy import create_engine, text

    from app import migrate

    monkeypatch.setattr(migrate, "LOCK_WAIT_SECONDS", 2)
    monkeypatch.setattr(migrate, "migration_engine", lambda: create_engine(url))
    holder = create_engine(url).connect()
    try:
        holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": migrate._STARTUP_LOCK_KEY})
        holder.commit()
        start, ran = time.monotonic(), []
        with migrate.startup_lock():
            ran.append(True)
        assert ran and time.monotonic() - start < 10
    finally:
        holder.execute(text("SELECT pg_advisory_unlock_all()"))
        holder.commit()
        holder.close()


def test_pack_units_migration_recounts_outer_purchases_once(scratch_engine):
    """0019: purchases of an item mapped as an outer pack were stored in packs; they're
    re-counted in units (2 CTN of 24 -> 48 at cost / 24), their valuation layers with them."""
    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0018_task_records")
        conn.commit()
    mapping_cols = ("sales_account_code, purchase_account_code, inventory_account_code, tax_code, reorder_level, "
                    "cost, markup_percent, tax_rate, vat_amount, inc_vat, price_outer, mapping_confirmed, tracking")
    mapping_vals = "'3000', '4000', '1200', 'VAT5', 0, 0, 0, 5, 0, 0, 0, 1, 'Yes'"
    with scratch_engine.begin() as conn:
        for mid, sku, outer in (("m-ctn", "COLA-CTN", 24), ("m-pcs", "SOAP-1", 1)):
            conn.execute(text(f"INSERT INTO stock_product_mappings (id, company_id, sku, name, units_per_outer, {mapping_cols}) "
                              f"VALUES (:id, 'c1', :sku, :sku, :outer, {mapping_vals})"), {"id": mid, "sku": sku, "outer": outer})
        conn.execute(text("INSERT INTO stock_movements (id, company_id, mapping_id, movement_type, reference, quantity, unit_cost) VALUES "
                          "('s1', 'c1', 'm-ctn', 'purchase', 'P1', 2, 120), ('s2', 'c1', 'm-pcs', 'purchase', 'P2', 8, 3), "
                          "('s3', 'c1', 'm-ctn', 'sales_invoice', 'SALE-1', -10, 0)"))
        conn.execute(text("INSERT INTO inventory_valuation_layers (id, company_id, item_code, source_module, source_id, "
                          "quantity_in, quantity_remaining, unit_cost, valuation_method) VALUES "
                          "('l1', 'c1', 'COLA-CTN', 'purchase', 'P1', 2, 2, 120, 'FIFO')"))

    run_migrations(scratch_engine)
    run_migrations(scratch_engine)  # a second run must not scale again

    with scratch_engine.connect() as conn:
        moves = {r.id: r for r in conn.execute(text(
            "SELECT id, quantity, unit_cost, source_quantity, units_per_outer FROM stock_movements"))}
        layer = conn.execute(text("SELECT quantity_in, quantity_remaining, unit_cost FROM inventory_valuation_layers")).one()
    assert (float(moves["s1"].quantity), float(moves["s1"].unit_cost)) == (48, 5)
    assert (float(moves["s1"].source_quantity), float(moves["s1"].units_per_outer)) == (2, 24)
    assert (float(moves["s2"].quantity), moves["s2"].source_quantity) == (8, None)   # pack size 1: untouched
    assert float(moves["s3"].quantity) == -10                                         # sales were already pieces
    assert tuple(map(float, layer)) == (48, 48, 5)


def test_due_date_migration_fills_existing_documents(scratch_engine):
    """0021: the due date column is filled from each document's own field (purchases' "due"
    is an amount, so only their due_date counts)."""
    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0020_rota_records")
        conn.commit()
    rows = (("r1", "salesInvoices", '{"due_date": "2026-11-01"}'), ("r2", "bills", '{"due": "05/11/2026"}'),
            ("r3", "purchaseRecords", '{"due": 105, "due_date": "2026-11-09"}'), ("r4", "purchaseRecords", '{"due": 105}'),
            ("r5", "quotations", '{"due_date": "2026-11-01"}'))
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        for rid, collection, payload in rows:
            conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, payload, row_version) "
                              "VALUES (:i, 'c1', :c, :p, 1)"), {"i": rid, "c": collection, "p": payload})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        due = dict(conn.execute(text("SELECT id, due_date FROM app_data_records")).all())
    assert due == {"r1": "2026-11-01", "r2": "2026-11-05", "r3": "2026-11-09", "r4": None, "r5": None}


def test_open_work_status_migration_fills_existing_records(scratch_engine):
    """0022: workflow records' status is stamped into doc_status (lower case); documents and
    other collections are left alone."""
    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0021_document_due_date")
        conn.commit()
    rows = (("w1", "overtimeRequests", '{"status": " Pending "}'), ("w2", "employeeLoans", '{"status": "Closed"}'),
            ("w3", "leaveRequests", '{}'), ("w4", "products", '{"status": "Active"}'))
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        for rid, collection, payload in rows:
            conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, payload, row_version) "
                              "VALUES (:i, 'c1', :c, :p, 1)"), {"i": rid, "c": collection, "p": payload})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        status = dict(conn.execute(text("SELECT id, doc_status FROM app_data_records")).all())
    assert status == {"w1": "pending", "w2": "closed", "w3": None, "w4": None}


def test_open_flag_migration_fills_existing_documents(scratch_engine):
    """0023: doc_open for invoices, bills and purchases from status, amount and amount_paid."""
    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0022_open_work_status")
        conn.commit()
    rows = (("d1", "salesInvoices", "issued", 100, 0), ("d2", "salesInvoices", "issued", 100, 100),
            ("d3", "bills", "awaiting payment", 50, 10), ("d4", "purchaseRecords", "paid", 70, 0),
            ("d5", "quotations", "draft", 30, 0))
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        for rid, collection, status, amount, paid in rows:
            conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, payload, row_version, doc_status, "
                              "amount, amount_paid) VALUES (:i, 'c1', :c, '{}', 1, :s, :a, :p)"),
                         {"i": rid, "c": collection, "s": status, "a": amount, "p": paid})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        flags = {i: (None if v is None else bool(v)) for i, v in conn.execute(text("SELECT id, doc_open FROM app_data_records"))}
    assert flags == {"d1": True, "d2": False, "d3": True, "d4": False, "d5": None}


def test_parties_main_copy_migration_rewrites_records(scratch_engine):
    """0024: customers/suppliers' JSON is rewritten as their parties row regenerates it, and
    the rows (with extra) are refilled."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0023_document_open_flag")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('p1', 'c1', 'customers', 'Acme', :p, 1)"),
                     {"p": _json.dumps({"name": "Acme", "trn": "100-1", "credit_limit": "1,000", "notes": "VIP"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'p1'")).scalar()
        extra, trn = conn.execute(text("SELECT extra, trn FROM parties WHERE id = 'p1'")).one()
        row = conn.execute(text("SELECT * FROM parties WHERE id = 'p1'")).mappings().one()
    # 0024 rewrites the JSON; 0039 then empties it, as the row rebuilds it exactly.
    from app.parties import party_record

    assert payload == ""
    assert party_record(dict(row)) == {"name": "Acme", "trn": "1001", "credit_limit": 1000.0, "notes": "VIP"}
    assert (_json.loads(extra), trn) == ({"notes": "VIP"}, "1001")


def test_products_main_copy_migration_rewrites_records(scratch_engine):
    """0025: products' JSON is rewritten as their row regenerates it (image kept), rows refilled."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0024_parties_main_copy")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('q1', 'c1', 'products', 'K-1', :p, 1)"),
                     {"p": _json.dumps({"code": "K-1", "name": "Kit", "price": "1,500", "image": "data:x", "notes": "n"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'q1'")).scalar()
        extra, price = conn.execute(text("SELECT extra, selling_price FROM catalog_products WHERE id = 'q1'")).one()
    assert _json.loads(payload) == {"code": "K-1", "name": "Kit", "selling_price": 1500.0, "price": "1,500",
                                    "notes": "n", "image": "data:x"}
    assert (_json.loads(extra), float(price)) == ({"price": "1,500", "notes": "n"}, 1500.0)


def test_tasks_main_copy_migration_rewrites_records(scratch_engine):
    """0026: tasks' JSON is rewritten as their row regenerates it, rows (with extra) refilled."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0025_products_main_copy")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('t1', 'c1', 'tasks', 'T-1', :p, 1)"),
                     {"p": _json.dumps({"id": "T-1", "title": "Audit", "progress": "45", "due_date": "2026-11-01T00:00:00",
                                        "status": "Done", "color": "blue"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 't1'")).scalar()
        extra, is_open = conn.execute(text("SELECT extra, is_open FROM task_records WHERE id = 't1'")).one()
    # 0026 rewrites the JSON; 0037 then empties it, as the row rebuilds it exactly.
    from app.main_copy import record_from
    from app.task_records import FIELDS

    with scratch_engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM task_records WHERE id = 't1'")).mappings().one()
    assert payload == ""
    assert record_from(dict(row), FIELDS) == {"title": "Audit", "status": "Done", "progress": 45,
                                              "due_date": "2026-11-01", "id": "T-1", "color": "blue"}
    assert (_json.loads(extra), bool(is_open)) == ({"id": "T-1", "color": "blue"}, False)


def test_rota_main_copy_migration_rewrites_records(scratch_engine):
    """0027: rota assignments' JSON is rewritten as their row regenerates it, rows (with extra) refilled."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0026_tasks_main_copy")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('r1', 'c1', 'rotaAssignments', 'E1-2026-10-14', :p, 1)"),
                     {"p": _json.dumps({"id": "E1-2026-10-14", "employee_id": "E1", "date": "2026-10-14T00:00:00Z",
                                        "code": "OFF", "mark": "Off", "break_minutes": "0", "notes": "n"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'r1'")).scalar()
        extra, kind, day = conn.execute(text("SELECT extra, kind, day FROM rota_records WHERE id = 'r1'")).one()
        row = conn.execute(text("SELECT * FROM rota_records WHERE id = 'r1'")).mappings().one()
    # 0027 rewrites the JSON; 0038 then empties it, as the row rebuilds it exactly.
    from app.main_copy import record_from
    from app.rota_records import FIELDS

    assert payload == ""
    assert record_from(dict(row), FIELDS) == {"employee_id": "E1", "date": "2026-10-14", "code": "OFF", "break_minutes": 0,
                                    "id": "E1-2026-10-14", "mark": "Off", "notes": "n"}
    assert (_json.loads(extra), kind, day) == ({"id": "E1-2026-10-14", "mark": "Off", "notes": "n"}, "off", "2026-10-14")


def test_document_records_migration_fills_and_rewrites(scratch_engine):
    """0028: sales invoices / purchases get header rows; their JSON is rewritten (amounts as numbers, lines kept)."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0027_rota_main_copy")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('s1', 'c1', 'salesInvoices', 'INV-1', :p, 1)"),
                     {"p": _json.dumps({"invoice_no": "INV-1", "customer": "Acme", "date": "20/02/2026", "total": "1,050.00",
                                        "lines": [{"qty": 1}]})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 's1'")).scalar()
        number, day, total = conn.execute(text("SELECT number, doc_date, total FROM document_records WHERE id = 's1'")).one()
    assert _json.loads(payload) == {"invoice_no": "INV-1", "customer": "Acme", "date": "20/02/2026", "total": 1050.0,
                                    "lines": [{"qty": 1}]}
    assert (number, day, float(total)) == ("INV-1", "2026-02-20", 1050.0)


def test_request_records_migration_fills_without_rewriting(scratch_engine):
    """0029: requests get rows; their JSON is left exactly as it was."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0028_document_records")
        conn.commit()
    raw = _json.dumps({"id": "OT-9", "employee_id": "E9", "ot_hours": "3", "status": "Approved", "date": "2026-10-01"})
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('o1', 'c1', 'overtimeRequests', 'OT-9', :p, 1)"), {"p": raw})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'o1'")).scalar()
        hours, is_open, day = conn.execute(text("SELECT hours, is_open, request_date FROM request_records WHERE id = 'o1'")).one()
    assert payload in (raw, "")   # unchanged by 0029; 0036 then empties it (rebuilt from the row)
    assert (float(hours), bool(is_open), day) == (3.0, True, "2026-10-01")


def test_employees_main_copy_migration(scratch_engine):
    """0030: employees.extra is filled and the JSON gets the row's values (salary from the row)."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0029_request_records")
        conn.commit()
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO employees (id, company_id, employee_no, full_name, department, designation, basic_salary, "
                          "housing_allowance, transport_allowance, other_allowance, status, is_active) "
                          "VALUES ('e1', 'c1', 'E-1', 'Omar', 'Sales', 'Staff', 8000, 1500, 0, 0, 'active', 1)"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('a1', 'c1', 'employees', 'E-1', :p, 1)"),
                     {"p": _json.dumps({"id": "E-1", "name": "Omar", "department": "Sales", "salary": 0,
                                        "status": "Active", "passport_no": "P9"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        payload = conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'a1'")).scalar()
        extra = conn.execute(text("SELECT extra FROM employees WHERE id = 'e1'")).scalar()
    assert _json.loads(payload) == {"id": "E-1", "name": "Omar", "department": "Sales", "salary": 8000,
                                    "status": "Active", "passport_no": "P9"}
    assert _json.loads(extra) == {"id": "E-1", "status": "Active", "passport_no": "P9"}


def test_lookup_json_migration_empties_only_lossless_records(scratch_engine):
    """0034: a lookup entry whose row rebuilds its JSON exactly has the JSON emptied; downgrade restores it."""
    import json as _json

    from alembic import command
    from app.migrate import alembic_config

    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        command.downgrade(alembic_config(conn), "0033_lookup_records")
        conn.commit()
    doc = {"code": "BOX", "name": "Box", "notes": "x"}
    with scratch_engine.begin() as conn:
        conn.execute(text("INSERT INTO companies (id, name, country, currency, vat_rate, stock_mode, inventory_accounting) "
                          "VALUES ('c1', 'c1', 'UAE', 'AED', 5, 'with_stock', 'periodic')"))
        conn.execute(text("INSERT INTO app_data_records (id, company_id, collection, record_key, payload, row_version) "
                          "VALUES ('u1', 'c1', 'salesUnits', 'BOX', :p, 1)"), {"p": _json.dumps(doc)})
        conn.execute(text("INSERT INTO lookup_records (id, company_id, collection, record_key, code, name, extra) "
                          "VALUES ('u1', 'c1', 'salesUnits', 'BOX', 'BOX', 'Box', :e)"), {"e": _json.dumps({"notes": "x"})})
    run_migrations(scratch_engine)
    with scratch_engine.connect() as conn:
        assert conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'u1'")).scalar() == ""
        command.downgrade(alembic_config(conn), "0033_lookup_records")
        conn.commit()
        assert _json.loads(conn.execute(text("SELECT payload FROM app_data_records WHERE id = 'u1'")).scalar()) == doc
