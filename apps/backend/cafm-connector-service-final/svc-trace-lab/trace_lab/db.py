"""Trace Lab's two database engines, its schema state, and the house error envelope (G0 addendum 2.3).

Two engines, never one:

* The MAIN engine reads the application database (DB_URL) READ-ONLY. install_main_engine() builds
  it with svc-deepagents' own TLS helpers (imported, never edited) and installs it as
  src.database._engine BEFORE anything in this process first uses that module, so every
  svc-deepagents code path Trace Lab calls by import runs on it. Every connection carries the
  server setting default_transaction_read_only=on, so Postgres refuses any write from any code
  path, and the pool is small (2, no overflow) so Trace Lab can never crowd the database.
* The LAB engine reaches Trace Lab's own database (TRACE_LAB_DB_URL), schema tracelab.

STATE holds the last schema check (a migrate.SchemaState). While it is not ready every route
except /health and /status answers 503 through require_ready. The chat is never affected.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from .migrate import SchemaState
    from .settings import Settings

log = structlog.get_logger(__name__)

APPLICATION_NAME = "trace-lab"
MAIN_POOL_SIZE = 2
LAB_POOL_SIZE = 3
LAB_MAX_OVERFLOW = 2
MAIN_SERVER_SETTINGS: dict[str, str] = {"default_transaction_read_only": "on", "application_name": APPLICATION_NAME}
UNAVAILABLE_TEXT = "Trace Lab is not available right now. The chat is not affected."
RETRY_AFTER_S = 30

STATE: "SchemaState | None" = None
_main: AsyncEngine | None = None
_lab: AsyncEngine | None = None
_settings: "Settings | None" = None


# ── engines ───────────────────────────────────────────────────────────────────────────────

def _engine_for(url: str, *, pool_size: int, max_overflow: int, server_settings: dict[str, str]) -> AsyncEngine:
    """An asyncpg engine built the way svc-deepagents builds its own (src/database.py): sslmode
    stripped from the URL and turned into asyncpg's ssl context by its own helpers."""
    from src import database  # svc-deepagents, reused by import

    connect_args: dict[str, Any] = dict(database._async_engine_connect_args(url))
    connect_args["server_settings"] = dict(server_settings)
    return create_async_engine(
        database._strip_sslmode_from_async_url(url),
        echo=False,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )


def install_main_engine(settings: "Settings") -> AsyncEngine:
    """Build the read-only main engine and install it as src.database._engine, then build that
    module's session factory on it. Idempotent. Refuses (RuntimeError) when src.database already
    holds an engine or a session factory, because that one would be writable."""
    global _main, _settings
    from src import database  # svc-deepagents, reused by import

    _settings = settings
    if _main is not None:
        if database._engine is not _main:
            raise RuntimeError("src.database._engine was replaced after Trace Lab installed its read-only engine")
        return _main
    if database._engine is not None or database._session_factory is not None:
        raise RuntimeError("src.database was used before Trace Lab installed its read-only engine; "
                           "refusing to run on a writable engine")
    url = (settings.main_db_url or "").strip()
    if not url:
        raise ValueError("DB_URL is not set")
    engine = _engine_for(url, pool_size=MAIN_POOL_SIZE, max_overflow=0, server_settings=MAIN_SERVER_SETTINGS)
    database._engine = engine
    database.init_session_factory()
    _main = engine
    log.info("trace_lab.main_engine.installed", pool_size=MAIN_POOL_SIZE, read_only=True)
    return engine


def main_engine() -> AsyncEngine:
    """The read-only main engine. RuntimeError until install_main_engine has run."""
    if _main is None:
        raise RuntimeError("the read-only main engine is not installed")
    return _main


def lab_engine() -> AsyncEngine:
    """Trace Lab's own database (its own pool). Uses the settings install_main_engine was given,
    else reads them from the environment (the migrate CLI). RuntimeError when the URL is unset."""
    global _lab
    if _lab is None:
        from .settings import load

        settings = _settings if _settings is not None else load()
        url = (settings.lab_db_url or "").strip()
        if not url:
            raise RuntimeError("TRACE_LAB_DB_URL is not set")
        _lab = _engine_for(url, pool_size=LAB_POOL_SIZE, max_overflow=LAB_MAX_OVERFLOW,
                           server_settings={"application_name": APPLICATION_NAME})
    return _lab


async def dispose_engines() -> None:
    """Close both pools (the lifespan's shutdown)."""
    for engine in (_lab, _main):
        if engine is not None:
            await engine.dispose()


# ── state ─────────────────────────────────────────────────────────────────────────────────

def ready() -> bool:
    return STATE is not None and STATE.status == "ready"


def set_state(st: "SchemaState") -> "SchemaState":
    """Store the result of a schema check and log it. Problems go to the log, never to a response."""
    global STATE
    STATE = st
    if st.status == "ready":
        log.info("trace_lab.schema.verified", version=st.applied_version)
    else:
        log.warning("trace_lab.schema.unavailable", status=st.status, expected_version=st.expected_version,
                    applied_version=st.applied_version, pending=list(st.pending), problems=list(st.problems)[:20])
    return st


def http_error(status: int, reason: str, error: str, **extra: Any) -> HTTPException:
    """The house error envelope: {"detail": {"reason": <snake_code>, "error": <sentence>, **extra}}."""
    return HTTPException(status_code=status, detail={"reason": reason, "error": error, **extra})


def unavailable_body() -> dict[str, Any]:
    """The 503 detail. It never names a table or schema: the problems go only to the log."""
    st = STATE
    return {
        "reason": "trace_lab_unavailable",
        "error": UNAVAILABLE_TEXT,
        "available": False,
        "retryable": True,
        "status": st.status if st is not None else "unreadable",
        "expected_version": st.expected_version if st is not None else None,
        "applied_version": st.applied_version if st is not None else None,
        "checked_at": st.checked_at if st is not None else None,
    }


async def require_ready() -> None:
    """FastAPI dependency for every /api/trace-lab route except /status: 503 until the schema is
    ready. A state that is not ready is checked again when it is older than REVERIFY_AFTER_S."""
    st = await refresh_state()
    if st.status != "ready":
        raise HTTPException(status_code=503, detail=unavailable_body(),
                            headers={"Retry-After": str(RETRY_AFTER_S)})


def reset_for_tests() -> None:
    """Forget the engines and the state, and take the main engine back out of src.database when
    this module installed it. Never disposes (tests own their engines)."""
    global STATE, _main, _lab, _settings, _pinned
    database = sys.modules.get("src.database")
    if database is not None and _main is not None and getattr(database, "_engine", None) is _main:
        database._engine = None
        database._session_factory = None
        database.AsyncSessionLocal = None
    STATE = None
    _main = None
    _lab = None
    _settings = None
    _pinned = False


# ── re-checking and pinning the state (the lifespan and require_ready use these) ───────────

REVERIFY_AFTER_S = 30.0
VERIFY_TIMEOUT_S = 20.0
_pinned = False  # True after disable(): kept until the process restarts
_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None


def disable(reason: str) -> "SchemaState":
    """Take Trace Lab down in this process for a reason a re-check cannot fix (it is turned off,
    its settings are wrong, the read-only main engine could not be installed, or its database URL
    is unset). The state is 'unreadable' and stays so until the process restarts."""
    global _pinned
    from .migrate import error_state

    st = set_state(error_state(reason))
    _pinned = True
    return st


def _verify_lock() -> asyncio.Lock:
    """One lock per event loop, so concurrent requests share one re-check."""
    global _lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _lock is None or _lock_loop is not loop:
        _lock, _lock_loop = asyncio.Lock(), loop
    return _lock


def _fresh(st: "SchemaState | None", max_age_s: float) -> bool:
    if st is None:
        return False
    if _pinned or st.status == "ready":
        return True
    try:
        checked = datetime.strptime(st.checked_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc) - checked).total_seconds() < max_age_s


async def refresh_state(*, max_age_s: float = REVERIFY_AFTER_S) -> "SchemaState":
    """The current state. One that is not ready (and not pinned by disable) is verified again when
    it is older than max_age_s, so a migration Hussain applies starts working within 30 s without
    a restart; the stale-job sweep runs when the state turns ready. Never raises."""
    if _fresh(STATE, max_age_s):
        return STATE  # type: ignore[return-value]
    async with _verify_lock():
        before = STATE
        if _fresh(before, max_age_s):
            return before  # type: ignore[return-value]
        from . import jobs, migrate

        try:
            st = await asyncio.wait_for(migrate.verify(lab_engine()), timeout=VERIFY_TIMEOUT_S)
        except TimeoutError:
            st = migrate.error_state(f"verify timed out after {VERIFY_TIMEOUT_S:.0f}s")
        except Exception as exc:  # noqa: BLE001 - a failed check is a state, not a crash
            st = migrate.error_state(f"check failed: {str(exc)[:280]}")
        set_state(st)
        if st.status == "ready" and (before is None or before.status != "ready"):
            try:
                await jobs.mark_interrupted()
            except Exception as exc:  # noqa: BLE001
                log.warning("trace_lab.jobs.sweep_failed", error=str(exc)[:300])
        return st
