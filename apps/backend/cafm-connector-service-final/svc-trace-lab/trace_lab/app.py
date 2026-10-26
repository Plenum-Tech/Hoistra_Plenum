"""The Trace Lab service: its own FastAPI app, process and container (G0 addendum 2.1).

    uvicorn trace_lab.app:app --host 0.0.0.0 --port 8010 --root-path /backend/trace-lab

Importing this module does no database or network I/O and builds no engine. The lifespan runs
startup(): it reads the settings, installs the READ-ONLY main engine into src.database before
anything uses it, verifies Trace Lab's own schema (applies it only with TRACE_LAB_SCHEMA=apply on
an allowed scratch database), and marks stale jobs interrupted. It never runs svc-deepagents'
startup: no ensure_tables of any existing store, no trace purge, no checkpointer. startup()
never raises; while the schema is not ready every route except /health and /status answers 503.
"""
from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import APIRouter, FastAPI

from . import db, jobs, migrate, routes_conversations, routes_status
from . import review_labels, routes_golden, routes_judges, routes_mode_proposals, routes_monitor, routes_review
from . import routes_labelling, routes_runs, routes_taxonomy, spend
from .migrate import SchemaState
from .settings import Settings, load

log = structlog.get_logger(__name__)

SERVICE = "trace-lab"
API_PREFIX = "/api/trace-lab"
VERIFY_TIMEOUT_S = 20.0
APPLY_TIMEOUT_S = 60.0

#: Every Trace Lab router, each mounted under API_PREFIX. A later task adds its router here.
ROUTERS: list[APIRouter] = [
    routes_status.router,
    routes_conversations.router,
    spend.router,
    routes_golden.router,
    routes_review.router,
    routes_mode_proposals.router,
    routes_taxonomy.router,
    routes_judges.router,
    routes_monitor.router,
    routes_labelling.router,
    routes_runs.router,
]


async def startup(settings: Settings | None = None) -> SchemaState:
    """Boot. Never raises, and never runs svc-deepagents' startup."""
    t0 = time.perf_counter()
    try:
        s = settings if settings is not None else load()
    except ValueError as exc:
        return db.disable(f"settings: {exc}")
    if not s.enabled:
        return db.disable("Trace Lab is turned off (TRACE_LAB_ENABLED is not 1)")
    try:
        db.install_main_engine(s)
    except Exception as exc:  # noqa: BLE001 - no read-only engine means no Trace Lab
        return db.disable(f"main database: {str(exc)[:280]}")
    if not s.lab_db_url:
        return db.disable("TRACE_LAB_DB_URL is not set")
    try:
        st = await _check_schema(s)
    except Exception as exc:  # noqa: BLE001 - startup never raises
        st = migrate.error_state(f"startup failed: {str(exc)[:280]}")
    db.set_state(st)
    if st.status == "ready":
        try:
            await jobs.mark_interrupted()
            from . import runner  # golden runs: statuses follow their jobs, reservations return, grading restarts

            await runner.settle_after_restart()
        except Exception as exc:  # noqa: BLE001
            log.warning("trace_lab.jobs.sweep_failed", error=str(exc)[:300])
        try:
            from . import judges  # judge runs: a run whose job the sweep took away reads interrupted

            await judges.recover_after_restart()
        except Exception as exc:  # noqa: BLE001 - startup never raises; a failure here never hides the sweep above
            log.warning("trace_lab.judges.recover_failed", error=str(exc)[:300])
    log.info("trace_lab.startup.done", status=st.status, version=st.applied_version,
             ms=round((time.perf_counter() - t0) * 1000))
    return st


async def _check_schema(s: Settings) -> SchemaState:
    eng = db.lab_engine()
    if s.schema_mode == "apply":
        database = await asyncio.wait_for(migrate.current_database(eng), timeout=VERIFY_TIMEOUT_S)
        reason = migrate.apply_refusal(database, migrate.db_host(s.lab_db_url))
        if reason:
            log.warning("trace_lab.schema.apply_refused", database=database, reason=reason)
        else:
            try:
                return await asyncio.wait_for(
                    migrate.apply_pending(eng, applied_by=f"startup:{socket.gethostname()}"[:200]),
                    timeout=APPLY_TIMEOUT_S)
            except TimeoutError:
                return migrate.error_state(f"apply timed out after {APPLY_TIMEOUT_S:.0f}s")
    try:
        return await asyncio.wait_for(migrate.verify(eng), timeout=VERIFY_TIMEOUT_S)
    except TimeoutError:
        return migrate.error_state(f"verify timed out after {VERIFY_TIMEOUT_S:.0f}s")


async def shutdown() -> None:
    try:
        await db.dispose_engines()
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.shutdown_failed", error=str(exc)[:300])


def create_app(settings: Settings | None = None) -> FastAPI:
    """The app. settings=None means startup() reads them from the environment."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await startup(app.state.trace_lab_settings)
        try:
            yield
        finally:
            await shutdown()

    app = FastAPI(title="Hoist Trace Lab", version="0.1.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.trace_lab_settings = settings
    try:
        review_labels.register()  # human verdicts on golden answers become the grader's labels
    except Exception as exc:  # noqa: BLE001 - without it the grader reads "unvalidated"; the service still runs
        log.warning("trace_lab.review_labels.register_failed", error=str(exc)[:200])

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "service": SERVICE}

    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)
    # The allow-list proxy (Phase 3): outside API_PREFIX, needing no sign-in and no schema, answering
    # loopback callers only. Imported here so this module's import line stays as Phase 2 left it.
    from . import proxy

    app.include_router(proxy.router)
    return app


app = create_app()
