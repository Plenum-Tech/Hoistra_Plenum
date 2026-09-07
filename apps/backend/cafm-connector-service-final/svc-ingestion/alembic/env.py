"""
svc-ingestion/alembic/env.py

Alembic migration environment.
Reads DB_URL from environment — strips asyncpg driver for sync migrations.
Only migrates IngestionBase tables (new Sprint 2 tables).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Make src/ importable ──────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.ingestion import IngestionBase  # noqa: E402

# ── Alembic Config ────────────────────────────────────────────────────────────
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ── DB URL — strip asyncpg for sync migrations ────────────────────────────────
raw_url = os.environ.get("DB_URL", "")
if not raw_url:
    raise RuntimeError("DB_URL environment variable is not set")

# Convert postgresql+asyncpg:// → postgresql+psycopg2://
sync_url = raw_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = IngestionBase.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL only)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema="plenum_cafm",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema="plenum_cafm",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
