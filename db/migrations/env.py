"""Alembic environment: reads the database URL from the DATABASE_URL env var.

Falls back to the same local-dev default used by ``eoa.config.settings().database_url``
so migrations can be run standalone (e.g. `alembic upgrade head`) before the app
package is importable.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

DEFAULT_DATABASE_URL = "postgresql://eoa@127.0.0.1:5432/eoanalyst"  # no default password (Q6-13); set DATABASE_URL

# this is the Alembic Config object, which provides access to values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging, unless it's been disabled (e.g. in tests).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No SQLAlchemy ORM models in this project -- migrations are hand-written DDL.
target_metadata = None


def _database_url() -> str:
    """Resolve the DB URL from DATABASE_URL, coercing it to the psycopg3 SQLAlchemy driver."""
    url = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no live DB connection)."""
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live DATABASE_URL connection."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
