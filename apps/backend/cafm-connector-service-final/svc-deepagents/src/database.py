import ssl
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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
            # Health-check a pooled connection before handing it out, and retire connections
            # older than 30 min. Without pre-ping, a connection the DB idle-closed (common with
            # cloud Postgres) gets reused and fails with "connection is closed" on the next
            # transaction — exactly the error seen on run-stateful-with-files / create_ingest_batch.
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


async def get_session() -> AsyncSession:
    async with _get_session_factory()() as session:
        yield session


# Module-level session factory for use inside @tool functions
AsyncSessionLocal = None


def init_session_factory():
    global AsyncSessionLocal
    AsyncSessionLocal = _get_session_factory()
