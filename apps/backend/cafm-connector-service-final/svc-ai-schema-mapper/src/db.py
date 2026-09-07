"""Database connection and session management.

Async session factory for query operations.
Sync connection string getter for PostgresSaver checkpointer (requires psycopg2, not asyncpg).
"""

import asyncio
import logging
import re
import ssl
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings

# Silence SQLAlchemy's per-query SQL output at module load time so it takes
# effect before any engine is created and before configure_logging() runs.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)


def _suppress_sql_loggers() -> None:
    """Force SQLAlchemy loggers to WARNING.

    Must be called after every engine creation because SQLAlchemy's echo=False
    resets the engine logger to NOTSET (inherits from root), undoing any earlier
    suppression.  Calling here guarantees it runs after the engine is built.
    """
    for name in (
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        "sqlalchemy.pool",
        "sqlalchemy.dialects",
        "sqlalchemy.orm",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)


def _async_engine_connect_args(db_url: str) -> dict:
    """Build asyncpg connect_args; strip sslmode from URL query (asyncpg rejects it)."""
    parsed = urlparse(db_url)
    host = parsed.hostname or ""
    qs = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = (qs.pop("sslmode", [None])[0] or "").lower()
    needs_ssl = sslmode in ("require", "verify-ca", "verify-full") or (
        not sslmode and ("postgres.database.azure.com" in host or ".azure." in host)
    )
    return {"ssl": ssl.create_default_context()} if needs_ssl else {}


def _strip_sslmode_from_async_url(db_url: str) -> str:
    parsed = urlparse(db_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("sslmode", None)
    flat = {k: v[-1] if v else "" for k, v in qs.items()}
    query = urlencode(flat)
    return urlunparse(parsed._replace(query=query))


# One engine (and its connection pool) is reused PER EVENT LOOP. Without this, get_async_engine
# built a brand-new engine — and a fresh, undisposed connection pool — on EVERY call (each
# get_async_session_factory(), e.g. every _sync_run_activity during a migration). That storms the
# database with new SSL connections (which Azure Postgres intermittently drops mid-handshake with
# "unexpected connection_lost() call") and leaks pools. Keying by the loop OBJECT (not id()) keeps
# per-loop isolation — asyncpg connections are bound to the loop that created them — while avoiding
# id() reuse after a loop is GC'd. Dead loops leave a small stale entry; they're never handed to a
# live loop.
_ENGINE_BY_LOOP: "dict[object, AsyncEngine]" = {}


def _build_async_engine() -> AsyncEngine:
    """Create a fresh async SQLAlchemy engine (uncached)."""
    settings = get_settings()
    db_url = _strip_sslmode_from_async_url(settings.db_url)
    # This engine serves the HTTP API (status polls, gate resumes, exports) — all queries here are
    # meant to be short. Bound both connection establishment and per-statement time so a slow/hung
    # request FAILS FAST and returns its pooled connection instead of holding a slot for 60s. Under
    # frequent /status polling an unbounded query storms the pool: every slot stays checked out, the
    # pool grows to max, then must open a BRAND-NEW connection against an already-saturated Azure
    # Postgres, which itself hangs at the default 60s asyncpg connect timeout (the failure we saw).
    #   timeout        — cap the asyncpg TCP/SSL handshake (default 60s → 15s).
    #   command_timeout — cap each statement so no single query pins a connection open (→ 45s).
    # These belong ONLY on the API engine, NOT on _async_engine_connect_args (shared with the
    # migration graph nodes, whose full-dataset introspection queries legitimately run long).
    connect_args = {
        **_async_engine_connect_args(settings.db_url),
        "timeout": 15,
        "command_timeout": 45,
    }

    # NullPool doesn't support pool_size/max_overflow parameters
    if settings.environment == "development":
        engine = create_async_engine(
            db_url,
            echo=False,
            poolclass=NullPool,
            # Validate a connection before handing it out — a cloud DB that idle-closed the
            # socket is transparently reconnected instead of raising connection_lost mid-query.
            pool_pre_ping=True,
            connect_args=connect_args,
        )
    else:
        engine = create_async_engine(
            db_url,
            echo=False,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Health-check pooled connections + retire them well under typical cloud idle timeouts,
            # so a stale connection is recycled rather than used and blown up (connection_lost).
            pool_pre_ping=True,
            pool_recycle=1800,
            # Don't block a request for the default 30s waiting on the pool; fail fast (with the
            # bounded connect timeout above) so the HTTP worker is freed to serve the next poll.
            pool_timeout=10,
            connect_args=connect_args,
        )
    _suppress_sql_loggers()
    return engine


def get_async_engine():
    """Return the async SQLAlchemy engine for the current event loop (created once per loop)."""
    try:
        loop: object = asyncio.get_running_loop()
    except RuntimeError:
        loop = None  # no running loop (sync context) — single shared slot
    engine = _ENGINE_BY_LOOP.get(loop)
    if engine is None:
        engine = _build_async_engine()
        _ENGINE_BY_LOOP[loop] = engine
    return engine


def get_async_session_factory():
    """Get async session factory."""
    engine = get_async_engine()
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )


async def get_async_session() -> AsyncSession:
    """Get async database session (dependency injection)."""
    session_factory = get_async_session_factory()
    async with session_factory() as session:
        yield session


async def get_plenum_cafm_columns_by_table() -> dict[str, set[str]]:
    """Map each plenum_cafm base table → its column names (lowercased).

    Used to constrain field matching to the routed target table's columns, so a
    source column only maps to a column that actually exists on its target table.
    Best-effort: returns {} on any failure (callers then skip the constraint).
    """
    out: dict[str, set[str]] = {}
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            res = await session.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = 'plenum_cafm' "
                    "ORDER BY table_name, ordinal_position"
                )
            )
            for tbl, col in res.fetchall():
                out.setdefault(str(tbl).lower(), set()).add(str(col).lower())
    except Exception:
        logging.getLogger(__name__).warning(
            "[db] get_plenum_cafm_columns_by_table failed — matching will not be constrained",
            exc_info=True,
        )
    return out


async def get_plenum_cafm_column_types_by_table() -> dict[str, dict[str, str]]:
    """Map each plenum_cafm base table → ``{column_name (lowercased): data_type}``.

    Used to make field matching TYPE-AWARE: a source column whose values are text /
    categorical / dates must not be matched onto a numeric (integer/numeric) destination
    column (e.g. frequency='Quarterly' → frequency_value INTEGER), which the DB rejects.
    Best-effort: returns {} on any failure (callers then skip the type constraint).
    """
    out: dict[str, dict[str, str]] = {}
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            res = await session.execute(
                text(
                    "SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = 'plenum_cafm' "
                    "ORDER BY table_name, ordinal_position"
                )
            )
            for tbl, col, dtype in res.fetchall():
                out.setdefault(str(tbl).lower(), {})[str(col).lower()] = str(dtype).lower()
    except Exception:
        logging.getLogger(__name__).warning(
            "[db] get_plenum_cafm_column_types_by_table failed — matching will not be "
            "type-constrained",
            exc_info=True,
        )
    return out


_SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")


async def get_plenum_cafm_sample_values_by_table(
    tables, *, allow: dict[str, set[str]] | None = None, limit: int = 50
) -> dict[str, dict[str, list[str]]]:
    """For each requested plenum_cafm base table return ``{column: [distinct sample values]}``
    from the rows that already exist there.

    Used by the combined mapping scorer to value-check a candidate mapping against the
    DESTINATION's real data, so a name-only match (e.g. ``vendors.id``) can't auto-merge
    columns whose values don't actually overlap. Best-effort: an empty / unselectable table
    yields nothing for that table; any failure returns ``{}`` (caller treats value as unknown).

    Table names are validated against ``_SAFE_IDENT`` and (when provided) the ``allow`` set of
    known base tables before being interpolated — never raw user input.
    """
    out: dict[str, dict[str, list[str]]] = {}
    wanted = sorted({str(t).lower() for t in (tables or []) if _SAFE_IDENT.match(str(t).lower())})
    if not wanted:
        return out
    try:
        session_factory = get_async_session_factory()
        async with session_factory() as session:
            for tbl in wanted:
                if allow is not None and tbl not in allow:
                    continue
                try:
                    res = await session.execute(
                        text(f'SELECT * FROM plenum_cafm."{tbl}" LIMIT {int(limit)}')
                    )
                    cols = list(res.keys())
                    per_col: dict[str, set] = {c: set() for c in cols}
                    for row in res.fetchall():
                        for c, v in zip(cols, row):
                            if v is not None and str(v).strip():
                                per_col[c].add(str(v).strip())
                    table_vals = {c: sorted(vals)[:limit] for c, vals in per_col.items() if vals}
                    if table_vals:
                        out[tbl] = table_vals
                except Exception:  # table empty / not readable → skip, treat as unknown
                    continue
    except Exception:
        logging.getLogger(__name__).warning(
            "[db] get_plenum_cafm_sample_values_by_table failed — value check skipped",
            exc_info=True,
        )
        return {}
    return out


def get_sync_db_url() -> str:
    """Get synchronous database connection string for PostgresSaver.

    PostgresSaver requires a sync psycopg connection, not asyncpg.
    Strips the SQLAlchemy driver prefix and ensures Azure Postgres gets sslmode=require.
    """
    settings = get_settings()
    url = settings.db_url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg://", "postgresql://")

    if "sslmode=" not in url and (
        "postgres.database.azure.com" in url
        or ".azure." in url
    ):
        url = f"{url}{'&' if '?' in url else '?'}sslmode=require"

    return url
