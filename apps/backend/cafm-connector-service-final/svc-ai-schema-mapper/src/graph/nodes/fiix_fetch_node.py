"""Node 1 — Fiix Data Ingestion: Fetch all records from Fiix API.

Runs the synchronous FiixDataConnector in a thread executor so the async
event loop is not blocked during the potentially long network fetch.

State consumed:  ingestion_id, fiix_subdomain/app_key/access_key/secret_key
State produced:  fetched_objects, fetch_stats, total_records_fetched, fetch_errors
"""

import asyncio
from datetime import datetime

from cafm_shared.logging import get_logger

from ..fiix_state import FiixIngestionState
from ...connectors.fiix_data_connector import FiixDataConnector, OBJECT_TABLE_MAP
from ...config import get_settings

logger = get_logger(__name__)


async def fiix_fetch_node(state: FiixIngestionState) -> FiixIngestionState:
    """
    Node 1: Connect to Fiix and fetch ALL records for every mapped object.

    Uses asyncio.to_thread() to run the synchronous Fiix API calls without
    blocking the event loop.  A per-object timeout of 600 s is enforced;
    individual object failures are logged but don't abort the whole run.
    """
    ingestion_id = state.get("ingestion_id", "unknown")
    logger.info(f"[FiixFetch] Node 1 start — ingestion_id={ingestion_id}")

    state["status"] = "fetching"
    state["current_node"] = 1
    state["notes"] = state.get("notes", []) + ["Node 1: fetching Fiix data"]

    # ── Resolve Fiix credentials ──────────────────────────────────────────────
    settings = get_settings()
    subdomain   = state.get("fiix_subdomain")   or settings.fiix_subdomain
    app_key     = state.get("fiix_app_key")     or settings.fiix_app_key
    access_key  = state.get("fiix_access_key")  or settings.fiix_access_key
    secret_key  = state.get("fiix_secret_key")  or settings.fiix_secret_key
    timeout     = settings.fiix_timeout         or 3600

    if not all([subdomain, app_key, access_key, secret_key]):
        msg = "Fiix credentials missing — check FIIX_* env vars"
        logger.error(f"[FiixFetch] {msg}")
        state["error_message"] = msg
        state["error_node"] = 1
        state["status"] = "failed"
        return state

    # ── Run fetch in executor (sync → async bridge) ───────────────────────────
    def _do_fetch() -> tuple:
        connector = FiixDataConnector(
            subdomain=subdomain,
            app_key=app_key,
            access_key=access_key,
            secret_key=secret_key,
            timeout=timeout,
        )
        if not connector.api.test_connection():
            raise ConnectionError(f"Cannot reach Fiix API at {subdomain}.macmms.com")

        return connector.fetch_all_data()

    try:
        fetched, stats, errors = await asyncio.to_thread(_do_fetch)
    except Exception as exc:
        logger.exception(f"[FiixFetch] Fatal fetch error: {exc}")
        state["error_message"] = str(exc)
        state["error_node"] = 1
        state["status"] = "failed"
        return state

    total = sum(stats.values())

    logger.info(
        f"[FiixFetch] ✓ Complete — {total} records from "
        f"{len(fetched)} objects, {len(errors)} object errors"
    )
    for obj_name, count in stats.items():
        logger.info(f"[FiixFetch]   {obj_name}: {count}")

    state["fetched_objects"] = fetched
    state["fetch_stats"] = stats
    state["total_records_fetched"] = total
    state["fetch_errors"] = errors
    state["notes"] = state.get("notes", []) + [
        f"Fetched {total} records from {len(fetched)} Fiix objects"
    ]

    # Persist progress to DB
    await _write_progress(state, total, stats, errors)

    return state


async def _write_progress(
    state: FiixIngestionState,
    total: int,
    stats: dict,
    errors: list,
) -> None:
    """Update FiixIngestionJob with fetch results."""
    db_session = state.get("db_session")
    ingestion_id = state.get("ingestion_id")
    if not db_session or not ingestion_id:
        return
    try:
        from sqlalchemy import update as sa_update
        from ...models.migration import FiixIngestionJob
        from uuid import UUID

        await db_session.execute(
            sa_update(FiixIngestionJob)
            .where(FiixIngestionJob.id == UUID(ingestion_id))
            .values(
                status="preprocessing",
                current_step="1_fetch_complete",
                total_records_fetched=total,
                fetch_stats=stats,
                fetch_errors=errors,
                progress_pct=33.0,
            )
        )
        await db_session.commit()
    except Exception as exc:
        logger.warning(f"[FiixFetch] DB progress write failed (non-fatal): {exc}")
