from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .core.logging import get_logger
from .models.base import Base
from . import models as _models  # noqa: F401

log = get_logger(__name__)

engine = create_async_engine(
    settings.db_url or "postgresql+asyncpg://cafm:cafm@localhost:5432/cafm_connectors",
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


async def init_db() -> None:
    log.info("db.init", status="starting")
    if settings.auto_migrate_on_startup:
        await apply_sql_migrations()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("db.init", status="complete")


_DOLLAR_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements on top-level ``;``.

    Dollar-quote aware: a ``;`` inside a ``$tag$ … $tag$`` block (PL/pgSQL function
    bodies, ``DO`` blocks) is NOT a statement terminator — otherwise the splitter shreds
    such blocks into fragments that each fail (append-only audit triggers, etc.). Full-line
    ``--`` comments are stripped only when outside a dollar-quoted block.
    """
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None  # e.g. "$fn$" while inside a dollar-quoted block

    for line in sql.splitlines():
        if dollar_tag is None and line.strip().startswith("--"):
            continue
        buf.append(line)
        # Toggle in/out of dollar-quoted blocks as tags open and close on this line.
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            if dollar_tag is None:
                dollar_tag = tag
            elif tag == dollar_tag:
                dollar_tag = None
            pos = m.end()
        # A ';' ends a statement only at top level (not inside a dollar-quoted block).
        if dollar_tag is None and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


async def apply_sql_migrations() -> None:
    """Apply all idempotent *.sql migrations in migrations/ (sorted by name)."""
    if not _MIGRATIONS_DIR.exists():
        log.warning("db.migration.dir_missing", path=str(_MIGRATIONS_DIR))
        return
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    total = 0
    for mig in files:
        statements = _split_sql(mig.read_text(encoding="utf-8"))
        for stmt in statements:
            # Each statement in its OWN transaction: one bad statement (e.g. a
            # PL/pgSQL dollar-quoted block that _split_sql can't split cleanly) must
            # not abort the shared transaction and poison every later migration.
            # exec_driver_sql (raw DBAPI) — NOT text(): several seeds use PostgreSQL
            # ``::jsonb`` casts, which text() misreads as ``:jsonb`` bind params.
            try:
                async with engine.begin() as conn:
                    await conn.exec_driver_sql(stmt)
                total += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "db.migration.stmt_skipped",
                    file=mig.name,
                    error=str(exc)[:300],
                )
        log.info("db.migration.file_applied", file=mig.name, statements=len(statements))
    log.info("db.migration.applied", statements=total, files=len(files))


async def apply_sql_seed(name: str) -> int:
    """Apply one idempotent seed file from seeds/ (statement by statement). Returns applied count."""
    path = _SEEDS_DIR / name
    if not path.exists():
        log.warning("db.seed.missing", path=str(path))
        return 0
    applied = 0
    for stmt in _split_sql(path.read_text(encoding="utf-8")):
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(stmt)
            applied += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("db.seed.stmt_skipped", file=name, error=str(exc)[:300])
    log.info("db.seed.applied", file=name, statements=applied)
    return applied


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
