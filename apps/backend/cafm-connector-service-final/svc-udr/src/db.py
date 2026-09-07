import ssl
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from .config import settings

_engine = None
_session_factory = None


def _async_engine_connect_args(db_url: str) -> dict:
    """asyncpg connect_args. `sslmode` is a libpq/psycopg param asyncpg rejects — we
    strip it from the URL and translate to asyncpg's `ssl` context when SSL is needed."""
    parsed = urlparse(db_url)
    host = parsed.hostname or ""
    qs = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = (qs.pop("sslmode", [None])[0] or "").lower()
    needs_ssl = sslmode in ("require", "verify-ca", "verify-full") or (
        not sslmode and ("postgres.database.azure.com" in host or ".azure." in host)
    )
    return {"ssl": ssl.create_default_context()} if needs_ssl else {}


def _strip_sslmode_from_async_url(db_url: str) -> str:
    """Remove sslmode (libpq-only) from the URL query — asyncpg rejects it as a kwarg."""
    parsed = urlparse(db_url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("sslmode", None)
    flat = {k: v[-1] if v else "" for k, v in qs.items()}
    return urlunparse(parsed._replace(query=urlencode(flat)))


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _strip_sslmode_from_async_url(settings.db_url),
            echo=False,
            pool_size=5,
            max_overflow=10,
            # Health-check pooled connections + retire after 30 min so an idle-closed cloud-DB
            # connection is transparently reconnected instead of failing with "connection is closed".
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args=_async_engine_connect_args(settings.db_url),
        )
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(_get_engine(), expire_on_commit=False)
    return _session_factory


async def get_session() -> AsyncSession:  # type: ignore[override]
    async with _get_session_factory()() as session:
        yield session


# ── Schema bootstrap (WP-4: UDR run versioning) ───────────────────────────────

def _udr_ddl() -> list[str]:
    schema = settings.db_schema
    return [
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        # WP-4: UDR run versioning
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.udr_run_versions (
            id               UUID PRIMARY KEY,
            session_id       TEXT NOT NULL,
            organization_id  UUID,
            version_no       INTEGER NOT NULL,
            custom_name      TEXT,
            phase            TEXT,
            mapping_status   TEXT,
            hierarchy_status TEXT,
            migration_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
            document_ids     JSONB NOT NULL DEFAULT '[]'::jsonb,
            batch_ids        JSONB NOT NULL DEFAULT '[]'::jsonb,
            snapshot         JSONB,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_udr_run_versions_session
            ON {schema}.udr_run_versions (session_id, version_no DESC)
        """,
        # WP-3: customer-named saved spaces (cross-device)
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.saved_spaces (
            id               UUID PRIMARY KEY,
            organization_id  UUID,
            name             TEXT NOT NULL,
            kind             TEXT NOT NULL DEFAULT 'custom',
            created_by       TEXT,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_saved_spaces_org
            ON {schema}.saved_spaces (organization_id, created_at DESC)
        """,
    ]


async def ensure_udr_tables() -> None:
    """Create the UDR support tables if they do not exist (idempotent)."""
    engine = _get_engine()
    async with engine.begin() as conn:
        for stmt in _udr_ddl():
            await conn.execute(text(stmt))
