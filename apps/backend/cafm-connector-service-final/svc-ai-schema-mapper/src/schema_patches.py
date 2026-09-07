"""Idempotent schema patches for migration_jobs (when Alembic history is out of sync)."""

import logging

from sqlalchemy import text

from .db import get_async_engine

logger = logging.getLogger(__name__)


async def ensure_migration_jobs_schema_patches() -> None:
    """Add columns introduced after initial deploy without requiring Alembic upgrade."""
    engine = get_async_engine()
    patches = [
        (
            "field_mapping_draft",
            "ALTER TABLE plenum_cafm.migration_jobs "
            "ADD COLUMN IF NOT EXISTS field_mapping_draft JSONB",
        ),
        (
            "output_structure_md_url",
            "ALTER TABLE plenum_cafm.migration_jobs "
            "ADD COLUMN IF NOT EXISTS output_structure_md_url TEXT",
        ),
    ]
    async with engine.begin() as conn:
        for name, ddl in patches:
            try:
                await conn.execute(text(ddl))
                logger.info("migration_jobs schema patch applied: %s", name)
            except Exception as exc:
                logger.warning("migration_jobs schema patch skipped (%s): %s", name, exc)
