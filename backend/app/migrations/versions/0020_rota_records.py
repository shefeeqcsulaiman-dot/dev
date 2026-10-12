"""rota_records: rota assignments as real columns (step A of moving them out of JSON).

app/rota_records.py keeps one row per "rotaAssignments" app-data record on every write;
this creates the table and fills it from the rota already stored. Days off, unpaid breaks
and the ESS rota then find an employee's days through (company, employee, day) instead of
decoding the company's whole rota for the range.

Revision ID: 0020_rota_records
Revises: 0019_stock_movement_pack_units
Create Date: 2026-10-12
"""
import sqlalchemy as sa
from alembic import op

revision = "0020_rota_records"
down_revision = "0019_stock_movement_pack_units"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "employee_no", "employee_name", "department", "location",
            "day", "kind", "code", "shift_type", "start_time", "end_time", "break_minutes", "status")


def upgrade() -> None:
    bind = op.get_bind()
    if "rota_records" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "rota_records",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("record_key", sa.String(160)),
            sa.Column("employee_no", sa.String(80)),
            sa.Column("employee_name", sa.String(160)),
            sa.Column("department", sa.String(120)),
            sa.Column("location", sa.String(120)),
            sa.Column("day", sa.String(10)),
            sa.Column("kind", sa.String(10), nullable=False, server_default="shift"),
            sa.Column("code", sa.String(20)),
            sa.Column("shift_type", sa.String(80)),
            sa.Column("start_time", sa.String(8)),
            sa.Column("end_time", sa.String(8)),
            sa.Column("break_minutes", sa.Float()),
            sa.Column("status", sa.String(40)),
        )
        op.create_index("ix_rota_records_company_employee_day", "rota_records", ["company_id", "employee_no", "day"])
        op.create_index("ix_rota_records_company_day", "rota_records", ["company_id", "day"])
    bind.execute(sa.text("DELETE FROM rota_records"))
    from app.rota_records import mirror

    table = sa.table("rota_records", *[sa.column(c, sa.Float()) if c == "break_minutes" else sa.column(c)
                                       for c in _COLUMNS])
    mirror.refill_all(bind, table)


def downgrade() -> None:
    op.drop_table("rota_records")
