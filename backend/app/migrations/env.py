"""Alembic environment. Uses the app's own engine settings and models, so the
`alembic` CLI and app.migrate.run_migrations() always agree on the database."""
from alembic import context

import app.models  # noqa: F401  -- registers every table on Base.metadata
from app.database import Base, engine

target_metadata = Base.metadata


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite can't ALTER most things in place; batch mode rebuilds the table.
        render_as_batch=connection.dialect.name == "sqlite",
    )


def run_migrations_offline() -> None:
    context.configure(
        url=engine.url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        # Called from app.migrate with a connection that already holds the
        # migration lock.
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
