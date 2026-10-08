"""Report figure columns on app_data_records (fig_*), filled for existing records.

The dashboard, summary, VAT figures and branch performance used to decode every sales
invoice, purchase record, bill and expense on each request (reports.py
app_data_payloads_with_branch()). app/doc_index.py doc_figures() now stamps the figures
they total on every save; this adds the columns and fills them for what is already
stored, with the same rules, so the totals become SQL sums.

Revision ID: 0009_report_figures
Revises: 0008_scheduled_job_indexes
Create Date: 2026-10-08
"""
import json

import sqlalchemy as sa
from alembic import op

from app.doc_index import FIGURE_COLLECTIONS, doc_figures

revision = "0009_report_figures"
down_revision = "0008_scheduled_job_indexes"
branch_labels = None
depends_on = None

_BATCH = 1000
_COLUMNS = (
    ("fig_status", sa.String(40)),
    ("fig_ref", sa.String(160)),
    ("fig_gross", sa.Numeric(14, 2)),
    ("fig_net", sa.Numeric(14, 2)),
    ("fig_vat", sa.Numeric(14, 2)),
    ("fig_taxable", sa.Numeric(14, 2)),
    ("fig_paid", sa.Numeric(14, 2)),
)
_INDEX = "ix_app_data_company_collection_fig_ref"


def _load(payload):
    try:
        data = json.loads(payload or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("app_data_records")}
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column("app_data_records", sa.Column(name, type_, nullable=True))
    if _INDEX not in {ix["name"] for ix in inspector.get_indexes("app_data_records")}:
        op.create_index(_INDEX, "app_data_records", ["company_id", "collection", "fig_ref"])

    records = sa.table(
        "app_data_records",
        sa.column("id", sa.String), sa.column("collection", sa.String), sa.column("payload", sa.Text),
        *(sa.column(name, type_) for name, type_ in _COLUMNS),
    )
    update = (
        sa.update(records)
        .where(records.c.id == sa.bindparam("_id"))
        .values({name: sa.bindparam(name) for name, _ in _COLUMNS})
    )
    for collection in sorted(FIGURE_COLLECTIONS):
        last_id = ""
        while True:
            rows = bind.execute(
                sa.select(records.c.id, records.c.payload)
                .where(records.c.collection == collection, records.c.id > last_id)
                .order_by(records.c.id).limit(_BATCH)
            ).all()
            if not rows:
                break
            bind.execute(update, [{"_id": row_id, **doc_figures(_load(payload), collection)} for row_id, payload in rows])
            last_id = rows[-1][0]


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="app_data_records")
    for name, _type in reversed(_COLUMNS):
        op.drop_column("app_data_records", name)
