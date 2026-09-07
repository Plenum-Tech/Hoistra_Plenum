from logging.config import fileConfig
import os
import sys

from sqlalchemy import engine_from_config, pool
from alembic import context

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from models.work_order import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _migration_url() -> str:
    """Use DATABASE_URL from the container; Alembic requires a sync driver."""
    raw = (
        os.environ.get("DATABASE_URL", "").strip()
        or os.environ.get("DB_URL", "").strip()
        or config.get_main_option("sqlalchemy.url", "")
    )
    if not raw:
        return raw
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg2://" + raw[len(prefix) :]
    if raw.startswith("postgresql://"):
        return "postgresql+psycopg2://" + raw[len("postgresql://") :]
    return raw


def run_migrations_offline() -> None:
    url = _migration_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _migration_url()
    configuration = config.get_section(config.config_ini_section, {}) or {}
    configuration["sqlalchemy.url"] = url
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
