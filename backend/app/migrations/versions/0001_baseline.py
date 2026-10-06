"""Baseline: the schema as it stood when Alembic was adopted.

Intentionally empty. app.migrate brings a database to this point itself:
an empty database is created from the models, and a pre-Alembic one gets
Base.metadata.create_all() plus the frozen legacy patches in
app.main.ensure_schema_updates(). Every later schema change is its own
revision on top of this one.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-06
"""

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
