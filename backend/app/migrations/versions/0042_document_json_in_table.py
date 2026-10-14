"""Step D for documents (sales invoices, purchases, bills, quotations, expenses, payments):
their JSON is no longer stored (app/record_payloads.py).

Adds document_lines.data (each line exactly as saved) and refills document_lines, then
refills document_records (rows now keep what a column can't give back exactly, and mark
documents whose lines are in document_lines), then empties each document's stored JSON --
only where its row and lines rebuild exactly the same JSON; downgrade stores it again.

Revision ID: 0042_document_json_in_table
Revises: 0041_product_json_in_table
Create Date: 2026-10-13
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision = "0042_document_json_in_table"
down_revision = "0041_product_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "collection", "record_key", "number", "party", "party_trn", "doc_date",
            "due_date", "status", "subtotal", "vat", "total", "paid", "salesperson", "location", "extra")
_AMOUNTS = {"subtotal", "vat", "total", "paid"}
_BATCH = 200


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("document_records", *[sa.column(c, sa.Numeric(18, 4)) if c in _AMOUNTS else sa.column(c)
                                          for c in _COLUMNS])
    lines = sa.table("document_lines", sa.column("record_id", sa.String), sa.column("line_no", sa.Integer),
                     sa.column("data", sa.Text))
    return records, rows, lines


def _stored(bind, rows_table, lines_table, ids):
    """{record id: rebuilt JSON} for these records, from their rows and lines."""
    from app.document_records import StoredDocument, rebuilt_document

    typed = {r["id"]: r for r in bind.execute(sa.select(rows_table).where(rows_table.c.id.in_(ids))).mappings()}
    lines: dict = {}
    for record_id, data in bind.execute(sa.select(lines_table.c.record_id, lines_table.c.data)
                                        .where(lines_table.c.record_id.in_(ids))
                                        .order_by(lines_table.c.record_id, lines_table.c.line_no)):
        lines.setdefault(record_id, []).append(json.loads(data) if data else None)
    return {i: rebuilt_document(StoredDocument(SimpleNamespace(**dict(row)), lines.get(i, [])))
            for i, row in typed.items()}


def upgrade() -> None:
    from app import doc_lines
    from app.document_records import COLLECTIONS, mirror

    bind = op.get_bind()
    if "data" not in {c["name"] for c in sa.inspect(bind).get_columns("document_lines")}:
        op.add_column("document_lines", sa.Column("data", sa.Text(), nullable=True))
    doc_lines.refill_all(bind)
    records, rows_table, lines_table = _tables()
    bind.execute(sa.text("DELETE FROM document_records"))
    mirror.refill_all(bind, rows_table)
    last_id = ""
    while True:
        batch = bind.execute(sa.select(records.c.id, records.c.payload)
                             .where(records.c.collection.in_(COLLECTIONS), records.c.id > last_id, records.c.payload != "")
                             .order_by(records.c.id).limit(_BATCH)).all()
        if not batch:
            break
        rebuilt = _stored(bind, rows_table, lines_table, [i for i, _ in batch])
        blank = []
        for row_id, payload in batch:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(doc, dict) and row_id in rebuilt and rebuilt[row_id] == doc:
                blank.append(row_id)
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = batch[-1][0]


def downgrade() -> None:
    bind = op.get_bind()
    records, rows_table, lines_table = _tables()
    ids = [i for (i,) in bind.execute(sa.select(records.c.id).join(rows_table, rows_table.c.id == records.c.id)
                                      .where(records.c.payload == ""))]
    for start in range(0, len(ids), _BATCH):
        for record_id, doc in _stored(bind, rows_table, lines_table, ids[start:start + _BATCH]).items():
            bind.execute(sa.update(records).where(records.c.id == record_id)
                         .values(payload=json.dumps(doc, ensure_ascii=False, default=str)))
    op.drop_column("document_lines", "data")
