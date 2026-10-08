"""Index stock_movements by (company_id, reference).

Every lookup by reference -- re-saving or deleting a sales invoice (SALE-<no>) or a
purchase, and the stock movements feed's check for invoices that already have real
movements -- scanned all of a company's stock movements. On 25,000 sales lines and
15,000 movements, one page of GET /app-data/stock-movements took 77 s without it.

Revision ID: 0012_stock_movement_ref_index
Revises: 0011_more_document_lines
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_stock_movement_ref_index"
down_revision = "0011_more_document_lines"
branch_labels = None
depends_on = None

_NAME = "ix_stock_movements_company_reference"


def upgrade() -> None:
    existing = {ix["name"] for ix in sa.inspect(op.get_bind()).get_indexes("stock_movements")}
    if _NAME not in existing:
        op.create_index(_NAME, "stock_movements", ["company_id", "reference"])


def downgrade() -> None:
    op.drop_index(_NAME, table_name="stock_movements")
