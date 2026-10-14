"""Uploaded purchase files and POS receipts out of JSON.

purchase_documents (app/purchase_documents.py): created (details as columns, the file in
`file`, the rest in extra) and filled. POS receipts ("posSales") get document_records rows
(app/document_records.py; their items stay in extra). Then each one's stored JSON is
emptied -- only where its row rebuilds exactly the same JSON; downgrade stores it again.

Revision ID: 0043_purchase_document_table
Revises: 0042_document_json_in_table
Create Date: 2026-10-14
"""
import json
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

revision = "0043_purchase_document_table"
down_revision = "0042_document_json_in_table"
branch_labels = None
depends_on = None

_COLUMNS = ("id", "company_id", "branch_id", "record_key", "name", "file_type", "size", "category", "period",
            "status", "uploaded_at", "extracted_at", "extra", "file")
_BATCH = 50  # each carries a whole file


def _tables():
    records = sa.table("app_data_records", sa.column("id", sa.String), sa.column("collection", sa.String),
                       sa.column("payload", sa.Text))
    rows = sa.table("purchase_documents", *[sa.column(c, sa.Numeric(18, 4)) if c == "size" else sa.column(c)
                                            for c in _COLUMNS])
    return records, rows


def _rebuild(row) -> dict:
    from app.purchase_documents import rebuilt_document

    return rebuilt_document(SimpleNamespace(**dict(row)))


def upgrade() -> None:
    from app.purchase_documents import mirror

    bind = op.get_bind()
    if "purchase_documents" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "purchase_documents",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), nullable=False),
            sa.Column("branch_id", sa.String(36)),
            sa.Column("record_key", sa.String(160)),
            sa.Column("name", sa.String(255)),
            sa.Column("file_type", sa.String(120)),
            sa.Column("size", sa.Numeric(18, 4)),
            sa.Column("category", sa.String(120)),
            sa.Column("period", sa.String(40)),
            sa.Column("status", sa.String(40)),
            sa.Column("uploaded_at", sa.String(40)),
            sa.Column("extracted_at", sa.String(40)),
            sa.Column("extra", sa.Text()),
            sa.Column("file", sa.Text()),
        )
        op.create_index("ix_purchase_documents_company", "purchase_documents", ["company_id"])
    records, rows_table = _tables()
    bind.execute(sa.text("DELETE FROM purchase_documents"))
    mirror.refill_all(bind, rows_table, batch=_BATCH)
    last_id = ""
    while True:
        batch = bind.execute(sa.select(records.c.id, records.c.payload)
                             .where(records.c.collection == "purchaseDocuments", records.c.id > last_id,
                                    records.c.payload != "")
                             .order_by(records.c.id).limit(_BATCH)).all()
        if not batch:
            break
        typed = {r["id"]: r for r in bind.execute(sa.select(rows_table).where(rows_table.c.id.in_([i for i, _ in batch])))
                 .mappings()}
        blank = []
        for row_id, payload in batch:
            try:
                doc = json.loads(payload or "{}")
            except (TypeError, ValueError):
                continue
            row = typed.get(row_id)
            if row is not None and isinstance(doc, dict) and _rebuild(row) == doc:
                blank.append(row_id)
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = batch[-1][0]
    _pos_sales(bind, records)


def _pos_sales(bind, records) -> None:
    """POS receipts into document_records (rows of receipts whose JSON is still stored are
    rewritten; a receipt already emptied keeps its row), then emptied where lossless."""
    from app.document_records import StoredDocument, document_rows, rebuilt_document

    cols = ("id", "company_id", "branch_id", "collection", "record_key", "number", "party", "party_trn", "doc_date",
            "due_date", "status", "subtotal", "vat", "total", "paid", "salesperson", "location", "extra")
    docs = sa.table("document_records", *[sa.column(c, sa.Numeric(18, 4)) if c in ("subtotal", "vat", "total", "paid")
                                          else sa.column(c) for c in cols])
    full = sa.table("app_data_records", sa.column("id"), sa.column("company_id"), sa.column("collection"),
                    sa.column("payload"), sa.column("branch_id"), sa.column("record_key"))
    last_id = ""
    while True:
        batch = bind.execute(sa.select(full.c.id, full.c.company_id, full.c.collection, full.c.payload, full.c.branch_id,
                                       full.c.record_key)
                             .where(full.c.collection == "posSales", full.c.id > last_id, full.c.payload != "")
                             .order_by(full.c.id).limit(500)).all()
        if not batch:
            return
        ids = [r[0] for r in batch]
        bind.execute(sa.delete(docs).where(docs.c.id.in_(ids)))
        rows = [row for rec in batch for row in document_rows(tuple(rec))]
        if rows:
            bind.execute(sa.insert(docs), rows)
        typed = {r["id"]: r for r in bind.execute(sa.select(docs).where(docs.c.id.in_(ids))).mappings()}
        blank = []
        for rec in batch:
            try:
                doc = json.loads(rec[3] or "{}")
            except (TypeError, ValueError):
                continue
            row = typed.get(rec[0])
            rebuilt = rebuilt_document(StoredDocument(SimpleNamespace(**dict(row)), [])) if row is not None else None
            if isinstance(doc, dict) and rebuilt == doc:
                blank.append(rec[0])
        if blank:
            bind.execute(sa.update(records).where(records.c.id.in_(blank)).values(payload=""))
        last_id = batch[-1][0]


def downgrade() -> None:
    bind = op.get_bind()
    records, rows_table = _tables()
    for row in bind.execute(sa.select(rows_table).join(records, records.c.id == rows_table.c.id)
                            .where(records.c.payload == "")).mappings().all():
        bind.execute(sa.update(records).where(records.c.id == row["id"])
                     .values(payload=json.dumps(_rebuild(row), ensure_ascii=False, default=str)))
    op.drop_table("purchase_documents")
    from app.document_records import StoredDocument, rebuilt_document

    docs = sa.table("document_records", *[sa.column(c) for c in (
        "id", "collection", "number", "party", "party_trn", "doc_date", "due_date", "status", "subtotal", "vat", "total",
        "paid", "salesperson", "location", "extra")])
    for row in bind.execute(sa.select(docs).join(records, records.c.id == docs.c.id)
                            .where(records.c.payload == "", docs.c.collection == "posSales")).mappings().all():
        doc = rebuilt_document(StoredDocument(SimpleNamespace(**dict(row)), []))
        bind.execute(sa.update(records).where(records.c.id == row["id"])
                     .values(payload=json.dumps(doc, ensure_ascii=False, default=str)))
