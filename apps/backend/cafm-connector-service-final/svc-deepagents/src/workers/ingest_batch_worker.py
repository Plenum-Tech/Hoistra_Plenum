"""Background processor for bulk ingest batches."""
from __future__ import annotations

import asyncio
from uuid import UUID
from pathlib import Path

import structlog

from ..agents.meta_tools import set_session_context
from ..agents.single_door_flow import (
    _file_kind,
    ingest_single_file,
    ingest_structured_batch,
    remove_files,
)
from ..config import settings
from ..services import ingest_batch_service as batch_svc
from ..http_client import caller_authorization
from ..services import building_binding, usage_events

log = structlog.get_logger(__name__)

_background_tasks: set[asyncio.Task] = set()
_cancelled_batches: set[str] = set()


def _as_uuid(value) -> UUID | None:
    try:
        return UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def schedule_ingest_batch(
    batch_id: str, building_id: str | None = None, *,
    authorization: str | None = None, actor: dict | None = None,
) -> None:
    """Start the batch in the background, carrying the caller with it.

    ``authorization`` is the user's own bearer token. The worker runs outside any request,
    so without this every call it makes to operations-intelligence would arrive with no
    caller and be refused — or, worse, be made with a credential that sees everything. The
    user who pressed upload is who this batch acts as. ``actor`` is what the receipts name.
    """
    task = asyncio.create_task(_run_batch(batch_id, building_id, authorization, actor or {}),
                               name=f"ingest-batch-{batch_id[:8]}")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def mark_batch_cancelled(batch_id: str) -> None:
    _cancelled_batches.add(batch_id)


async def _run_batch(
    batch_id: str, building_id: str | None = None,
    authorization: str | None = None, actor: dict | None = None,
) -> None:
    # Set inside the task, before anything else, so every sub-task copies it — the same
    # reason set_session_context() below sits where it does.
    caller_authorization.set(authorization)
    actor = actor or {}
    batch = await batch_svc.get_ingest_batch(batch_id)
    if not batch:
        log.warning("ingest_batch.worker.missing", batch_id=batch_id)
        return

    session_id = batch["session_id"]
    org = batch["organization_id"]
    cmms = batch.get("cmms_name") or "Custom"
    items = batch.get("items") or []
    file_paths = [str(i.get("file_path") or "") for i in items]

    # Bind this background task to the batch's session so the migration is registered on the session
    # workspace EARLY — the moment start_migration_multi creates it, inside ingest_structured_batch /
    # ingest_single_file (which register only when get_session_context() is set). The worker runs at
    # the default "shared" context, so without this the migration was registered only AFTER it was
    # driven to its first gate (post-Deterministic mapping) — which is why the entry point / workflow
    # queue appeared late instead of during File ingestion. Set inside the task, before the gather,
    # so each per-file/group sub-task copies it. Migration ids reach the panel via the FE's workspace
    # poll while the batch runs.
    set_session_context(session_id)

    await batch_svc.set_batch_status(batch_id, "running")
    sem = asyncio.Semaphore(max(1, settings.ingest_batch_concurrency))

    async def _process_index(index: int, file_path: str) -> None:
        if batch_id in _cancelled_batches:
            return
        current = await batch_svc.get_ingest_batch(batch_id)
        if current and current.get("status") == "cancelled":
            return
        async with sem:
            if batch_id in _cancelled_batches:
                return
            await batch_svc.update_batch_item(batch_id, index, status="running")
            try:
                result = await ingest_single_file(
                    file_path=file_path,
                    organization_id=org,
                    cmms_name=cmms,
                    # Run bulk files through the SAME interactive pipeline as a single-file upload:
                    # drive each to its first HITL gate, then pause so the user reviews/continues in
                    # the Migration panel (instead of auto-approving every gate headless).
                    interactive_migration=True,
                )
                item_status = str(result.get("status") or "")
                # "awaiting_gate" = the file reached its first gate and is paused for the user to
                # review/continue in the Migration panel — that's a SUCCESS for the ingest driver,
                # not a failure. Only a hard "error" counts as failed. NB: the running tally lives in
                # the DB via update_batch_item(increment, succeeded) — no local counter here (a bare
                # `succeeded += 1` on an unbound local crashed every COMPLETED file as an error).
                ok = item_status in ("done", "awaiting_gate")
                # Register the paused migration on the session so it surfaces in the Migration panel
                # / workflow queue (resolve_session_migration_ids reads migration_by_file). The
                # background worker has no request-scoped session context, so ingest_single_file's
                # own register step is skipped — do it here where session_id is known.
                mig_id = str(result.get("migration_id") or "")
                if ok and mig_id:
                    try:
                        from ..agents.session_workspace import register_migration_file
                        register_migration_file(
                            session_id, mig_id, str(result.get("file_name") or Path(file_path).name)
                        )
                    except Exception as reg_err:  # pragma: no cover - registration is best-effort
                        log.warning(
                            "ingest_batch.register_migration_failed",
                            batch_id=batch_id, migration_id=mig_id, error=str(reg_err),
                        )
                # Filed as soon as it is done, not at the end of the batch: a file that
                # succeeded can be bound, and a later failure then costs only itself.
                # Not `and building_id`: the file still earns its row when no building
                # was chosen, and bind_and_log returns without binding in that case.
                if ok:
                    await building_binding.bind_and_log(
                        building_id, result.get("tool_calls"),
                        where="batch", session_id=session_id,
                        file_paths=[str(file_path)])
                # The receipt for this file, whichever way it went: who, what, where, outcome.
                _uid = actor.get("user_id")
                await usage_events.record_ingestion_audit(
                    outcome="accepted" if ok else "rejected",
                    organization_id=_as_uuid(org), actor_user_id=_as_uuid(_uid),
                    actor_role=actor.get("role"), document_name=Path(file_path).name,
                    building_id=_as_uuid(building_id),
                    warning=None if ok else str(result.get("error") or "")[:500] or None,
                    detail={"session_id": session_id, "batch_id": batch_id, "where": "batch"},
                )
                if ok:
                    await usage_events.record_usage(
                        kind="ingest", organization_id=_as_uuid(org), user_id=_as_uuid(_uid),
                        building_id=_as_uuid(building_id),
                        detail={"session_id": session_id, "batch_id": batch_id, "file": Path(file_path).name},
                    )
                await batch_svc.update_batch_item(
                    batch_id,
                    index,
                    status=item_status or ("done" if ok else "error"),
                    kind=str(result.get("kind") or ""),
                    summary=str(result.get("summary") or ""),
                    error=result.get("error"),
                    increment=True,
                    succeeded=ok,
                )
            except Exception as exc:
                log.exception("ingest_batch.file.error", batch_id=batch_id, file=file_path)
                await batch_svc.update_batch_item(
                    batch_id,
                    index,
                    status="error",
                    summary="",
                    error=str(exc)[:300],
                    increment=True,
                    succeeded=False,
                )

    # ── Structured files → ONE migration ──────────────────────────────────────
    # All CSV/Excel files become source tables inside a SINGLE migration (start_migration_multi),
    # matching the inline multi-file path ("never one migration per spreadsheet"). Non-structured
    # files (documents) still process individually. structured_idx maps back to the batch items so
    # every file keeps its own row/status even though they share one migration_id.
    structured_idx = [i for i, p in enumerate(file_paths) if p and _file_kind(p) == "structured"]
    other_idx = [i for i, p in enumerate(file_paths) if p and _file_kind(p) != "structured"]

    async def _process_structured_group() -> None:
        if not structured_idx or batch_id in _cancelled_batches:
            return
        current = await batch_svc.get_ingest_batch(batch_id)
        if current and current.get("status") == "cancelled":
            return
        async with sem:
            if batch_id in _cancelled_batches:
                return
            for i in structured_idx:
                await batch_svc.update_batch_item(batch_id, i, status="running")
            try:
                result = await ingest_structured_batch(
                    file_paths=[file_paths[i] for i in structured_idx],
                    organization_id=org,
                    cmms_name=cmms,
                    interactive_migration=True,
                )
                item_status = str(result.get("status") or "")
                ok = item_status in ("done", "awaiting_gate")
                mig_id = str(result.get("migration_id") or "")
                # One migration_id shared by every structured file — register each file → the same
                # id so it surfaces once in the Migration panel (resolve_session_migration_ids
                # de-dupes to a single id). The worker has no request-scoped session context, so
                # ingest_structured_batch's own register is skipped — do it here.
                if ok and mig_id:
                    from ..agents.session_workspace import register_migration_file
                    for i in structured_idx:
                        try:
                            register_migration_file(
                                session_id, mig_id,
                                str((items[i] or {}).get("file_name") or Path(file_paths[i]).name),
                            )
                        except Exception as reg_err:  # pragma: no cover - best-effort
                            log.warning(
                                "ingest_batch.register_migration_failed",
                                batch_id=batch_id, migration_id=mig_id, error=str(reg_err),
                            )
                for i in structured_idx:
                    await batch_svc.update_batch_item(
                        batch_id, i,
                        status=item_status or ("done" if ok else "error"),
                        kind="structured",
                        summary=str(result.get("summary") or ""),
                        error=result.get("error"),
                        increment=True,
                        succeeded=ok,
                    )
            except Exception as exc:
                log.exception("ingest_batch.structured_group.error", batch_id=batch_id)
                for i in structured_idx:
                    await batch_svc.update_batch_item(
                        batch_id, i,
                        status="error", summary="",
                        error=str(exc)[:300],
                        increment=True, succeeded=False,
                    )

    try:
        await asyncio.gather(
            _process_structured_group(),
            *[_process_index(i, file_paths[i]) for i in other_idx],
        )
    except Exception as exc:
        await batch_svc.set_batch_status(batch_id, "failed", error_message=str(exc)[:500])
        log.exception("ingest_batch.worker.failed", batch_id=batch_id)
        return
    finally:
        _cancelled_batches.discard(batch_id)
        remove_files(file_paths)

    final = await batch_svc.get_ingest_batch(batch_id)
    if not final:
        return
    if final.get("status") == "cancelled":
        return

    from ..agents.session_workspace import record_batch_ingestion_complete

    record_batch_ingestion_complete(
        session_id,
        batch_id=batch_id,
        succeeded_count=int(final.get("completed_count") or 0),
    )
    log.info(
        "ingest_batch.worker.done",
        batch_id=batch_id,
        completed=final.get("completed_count"),
        failed=final.get("failed_count"),
    )
