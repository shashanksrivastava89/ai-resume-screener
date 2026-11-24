"""Alembic environment — wired to the app's own engine and metadata.

We do NOT read a URL from alembic.ini; instead we reuse
`resume_screener.db.get_engine()` (DATABASE_URL, with passwordless Entra auth in
prod) and `Base.metadata` (populated by importing the models package). This keeps
one connection story across the app, migrations and tests.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from resume_screener.db.base import Base
from resume_screener.db.engine import get_engine

# Importing the models package registers every ORM model on Base.metadata.
# (Empty in Phase 0; Phase 1 adds the tables and autogenerate picks them up.)
import resume_screener.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection (uses the configured URL string)."""
    from resume_screener.config import load_db_settings

    context.configure(
        url=load_db_settings().database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection from the app engine."""
    connectable = get_engine()
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
