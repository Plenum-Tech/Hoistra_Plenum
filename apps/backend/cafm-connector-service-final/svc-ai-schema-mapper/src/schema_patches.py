"""Idempotent schema patches for migration_jobs (when Alembic history is out of sync).

Startup DDL that cannot queue the service behind a lock. Measured 17 Sep 2026: an outside
session idle in a transaction held a share lock; every replica start ran an
``ADD COLUMN IF NOT EXISTS`` that needed an exclusive lock and waited, and each waiting ALTER
blocked every reader of its table behind it — 47 sessions queued within forty minutes.

Two rules. A patch whose column already exists is never executed: ``ADD COLUMN IF NOT EXISTS``
still takes ACCESS EXCLUSIVE before it notices, so ``information_schema`` is asked first. And a
patch that cannot get its lock in five seconds gives up, retries a few times, and is skipped
with a warning — it runs again on the next start; the service does not wait for it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from .db import get_async_engine

logger = logging.getLogger(__name__)

LOCK_TIMEOUT = "5s"
LOCK_RETRIES = 4
LOCK_RETRY_SLEEP_S = 3.0

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_ADD_COLUMN = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?:(?P<schema>{_IDENT})\.)?(?P<table>{_IDENT})\s+"
    rf"ADD\s+(?:COLUMN\s+)?IF\s+NOT\s+EXISTS\s+(?P<column>{_IDENT})\b",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_EXISTS_SQL = text(
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = COALESCE(:s, current_schema()) AND table_name = :t AND column_name = :c "
    "LIMIT 1"
)

PATCHES: list[tuple[str, str]] = [
    (
        "field_mapping_draft",
        "ALTER TABLE plenum_cafm.migration_jobs ADD COLUMN IF NOT EXISTS field_mapping_draft JSONB",
    ),
    (
        "output_structure_md_url",
        "ALTER TABLE plenum_cafm.migration_jobs ADD COLUMN IF NOT EXISTS output_structure_md_url TEXT",
    ),
]


def parse_add_column(stmt: str) -> tuple[str | None, str, str] | None:
    """(schema, table, column) for a plain ``ALTER TABLE … ADD COLUMN IF NOT EXISTS …``; else None."""
    m = _ADD_COLUMN.match(stmt or "")
    if not m:
        return None
    return m.group("schema"), m.group("table"), m.group("column")


def is_lock_timeout(exc: BaseException) -> bool:
    text_ = str(exc).lower()
    return "lock timeout" in text_ or "lock_timeout" in text_ or "locknotavailable" in text_


async def column_exists(engine: Any, schema: str | None, table: str, column: str) -> bool:
    """True when the column is already there. A failure to ask counts as "not known to exist"."""
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(_COLUMN_EXISTS_SQL, {"s": schema, "t": table, "c": column})).scalar()
        return row is not None
    except Exception:  # noqa: BLE001 — the ALTER itself is the authority; this is only a shortcut
        return False


async def apply_patch(
    engine: Any, name: str, ddl: str, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
) -> str:
    """One patch in its own transaction under the two rules.

    Returns "applied", "already_applied", "lock_skipped" or "failed".
    """
    parsed = parse_add_column(ddl)
    if parsed and await column_exists(engine, *parsed):
        logger.info("migration_jobs schema patch already applied, no lock taken: %s", name)
        return "already_applied"
    for attempt in range(1, LOCK_RETRIES + 1):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
                await conn.execute(text(ddl))
            logger.info("migration_jobs schema patch applied: %s", name)
            return "applied"
        except Exception as exc:  # noqa: BLE001
            if is_lock_timeout(exc):
                if attempt < LOCK_RETRIES:
                    logger.warning(
                        "migration_jobs schema patch %s: lock wait (attempt %d/%d), a reader holds the "
                        "table; backing off rather than queueing the service behind it",
                        name, attempt, LOCK_RETRIES,
                    )
                    await sleep(LOCK_RETRY_SLEEP_S)
                    continue
                logger.warning("migration_jobs schema patch %s skipped after %d lock timeouts; next start retries",
                               name, LOCK_RETRIES)
                return "lock_skipped"
            logger.warning("migration_jobs schema patch skipped (%s): %s", name, exc)
            return "failed"
    return "lock_skipped"  # unreachable; keeps the return type honest


async def ensure_migration_jobs_schema_patches() -> None:
    """Add columns introduced after initial deploy without requiring Alembic upgrade."""
    engine = get_async_engine()
    for name, ddl in PATCHES:
        await apply_patch(engine, name, ddl)
