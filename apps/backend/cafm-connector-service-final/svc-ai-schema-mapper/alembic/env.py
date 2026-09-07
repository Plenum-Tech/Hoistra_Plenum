"""Alembic environment configuration for svc-AI-Schema-Mapper migrations.

This file handles both offline (generate SQL) and online (execute immediately)
migration modes.
"""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool, text
from alembic import context
import sys
import os
from pathlib import Path

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from models.migration import MigrationBase

# this is the Alembic Config object, which provides
# the values of the [alembic] section of the alembic.ini
# file as Python dictionary for use in process_migration_env
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except (KeyError, Exception):
        # Skip logging config if it's not properly configured
        pass

# add your model's MetaData object for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = MigrationBase.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = config.get_main_option("sqlalchemy.url")

    with context.begin_transaction():
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=target_metadata,
            literal_binds=True,
            dialect_opts={"paramstyle": "named"},
        )

        with context.begin_transaction():
            context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section)

    # Try to get URL from alembic.ini first, then fall back to env var
    db_url = config.get_main_option("sqlalchemy.url")
    if not db_url or db_url.strip() == "":
        db_url = os.environ.get("DB_URL")
        if not db_url:
            raise RuntimeError(
                "Database URL not found. Set DB_URL environment variable or "
                "configure sqlalchemy.url in alembic.ini"
            )

    # Convert async URL to sync URL for alembic (alembic needs psycopg2, not asyncpg)
    if "postgresql+asyncpg://" in db_url:
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    configuration["sqlalchemy.url"] = db_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema="plenum_cafm",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
