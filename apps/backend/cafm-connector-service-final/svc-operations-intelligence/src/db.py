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

#: How many times a failing migration is retried after create_all. Three covers a chain of
#: three dependent migrations; a loop that never terminates would hang startup, which is
#: worse than a named unapplied file.
_MAX_MIGRATION_PASSES = 3

#: A concurrent index build has to run outside a transaction, so it is routed differently.
_CONCURRENT_INDEX = re.compile(r"CREATE\s+INDEX\s+CONCURRENTLY", re.IGNORECASE)

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


async def init_db() -> None:
    """Migrate, create the ORM tables, then re-run whatever the first pass could not apply.

    The two passes exist because the ordering is genuinely circular. Some migrations create
    things `create_all` needs — the schema itself, tables the ORM does not model — so they
    have to run first. Others ALTER tables the ORM owns, which on a fresh database do not
    exist until `create_all` has run, so those are skipped on the way past.

    Nobody noticed because the second boot fixed it: by then the tables existed and the
    ALTERs applied. A first-boot deployment ran with columns missing and failed with a 500
    that looked like a code fault — `compliance_certificates.site_ref does not exist` — until
    somebody restarted the service and it mysteriously healed.

    Only the files that actually failed are retried, so a healthy start still costs one pass.
    Every migration is written idempotent, which is what makes a second attempt safe.
    """
    log.info("db.init", status="starting")
    retry: list[Path] = []
    if settings.auto_migrate_on_startup:
        # Swept before the run, so an index left unfinished by a previous concurrent build
        # is rebuilt on this one rather than skipped forever by IF NOT EXISTS.
        swept = await _drop_invalid_indexes()
        if swept:
            log.warning("db.invalid_indexes_dropped", indexes=swept,
                        note="left unfinished by an earlier concurrent build; rebuilding")
        retry = await apply_sql_migrations()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Retried until nothing more succeeds, not just once. Filename order does not express
    # dependencies BETWEEN migrations: compliance_certificates_region.sql indexes a column
    # that compliance_certificates_state.sql adds, and `region` sorts before `state`, so in
    # any single pass that index can never build. Looping while the failure set shrinks
    # resolves those without anyone having to rename files into a working order — which is
    # a trap that re-opens every time a migration is added.
    passes = 0
    while retry and passes < _MAX_MIGRATION_PASSES:
        passes += 1
        log.info("db.init.migration_retry", pass_no=passes,
                 files=[f.name for f in retry])
        still_failing = await apply_sql_migrations(only=retry)
        if len(still_failing) >= len(retry):
            retry = still_failing
            break          # no progress: another pass will not help
        retry = still_failing
    if retry:
        # Not necessarily broken: a migration for a table another service owns will never
        # apply here. Named so it is a known absence rather than a silent one.
        log.warning(
            "db.init.migration_unapplied",
            files=[f.name for f in retry],
            note="still failing after the ORM tables were created and retries converged",
        )
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


async def _drop_invalid_indexes() -> list[str]:
    """Remove indexes a concurrent build left behind unfinished.

    This is the trap that comes with CREATE INDEX CONCURRENTLY. When a concurrent build
    fails part-way, Postgres keeps the index in an INVALID state: it exists, so
    ``IF NOT EXISTS`` skips it on every later run, and no query will use it. The result is
    an index that is permanently missing while looking permanently present.

    Dropping one is safe by definition — an invalid index is unusable, so nothing can be
    relying on it — and it lets the next migration pass build it properly.
    """
    dropped: list[str] = []
    try:
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            rows = await raw.driver_connection.fetch(
                """SELECT c.relname AS name
                   FROM pg_index i
                   JOIN pg_class c ON c.oid = i.indexrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'plenum_cafm' AND NOT i.indisvalid"""
            )
            for r in rows:
                name = r["name"]
                await raw.driver_connection.execute(
                    f'DROP INDEX IF EXISTS plenum_cafm."{name}"'
                )
                dropped.append(str(name))
    except Exception as exc:  # noqa: BLE001 — housekeeping must not stop startup
        log.warning("db.invalid_index_sweep_failed", error=str(exc)[:200])
    return dropped


async def apply_sql_migrations(only: list[Path] | None = None) -> list[Path]:
    """Apply the idempotent *.sql migrations in filename order.

    Returns the files that had at least one statement fail, so the caller can retry them
    once the ORM tables exist. ``only`` restricts the run to a previous pass's failures.
    """
    if not _MIGRATIONS_DIR.exists():
        log.warning("db.migration.dir_missing", path=str(_MIGRATIONS_DIR))
        return []
    files = list(only) if only is not None else sorted(_MIGRATIONS_DIR.glob("*.sql"))
    total = 0
    failed: list[Path] = []
    for mig in files:
        statements = _split_sql(mig.read_text(encoding="utf-8"))
        for stmt in statements:
            # Each statement in its OWN transaction: one bad statement (e.g. a
            # PL/pgSQL dollar-quoted block that _split_sql can't split cleanly) must
            # not abort the shared transaction and poison every later migration.
            # exec_driver_sql (raw DBAPI) — NOT text(): several seeds use PostgreSQL
            # ``::jsonb`` casts, which text() misreads as ``:jsonb`` bind params.
            try:
                if _CONCURRENT_INDEX.search(stmt):
                    # CREATE INDEX CONCURRENTLY cannot run inside a transaction block,
                    # and it is concurrent precisely so building an index on a live table
                    # does not hold a lock that blocks every write to it for the duration —
                    # on a register being ingested into, that is an outage.
                    #
                    # Run on the raw asyncpg connection rather than through SQLAlchemy.
                    # Neither engine.execution_options(isolation_level="AUTOCOMMIT") nor
                    # setting it on an open connection took effect on this pooled engine:
                    # the statement still arrived inside a transaction and every index
                    # build failed, silently, as a skipped migration. asyncpg's own execute
                    # opens no transaction, which is the behaviour actually needed.
                    async with engine.connect() as conn:
                        raw = await conn.get_raw_connection()
                        await raw.driver_connection.execute(stmt)
                else:
                    async with engine.begin() as conn:
                        await conn.exec_driver_sql(stmt)
                total += 1
            except Exception as exc:  # noqa: BLE001
                if mig not in failed:
                    failed.append(mig)
                log.warning(
                    "db.migration.stmt_skipped",
                    file=mig.name,
                    error=str(exc)[:300],
                )
        log.info("db.migration.file_applied", file=mig.name, statements=len(statements))
    log.info("db.migration.applied", statements=total, files=len(files),
             files_with_failures=len(failed))
    return failed


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
