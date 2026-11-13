"""Bulk ingest batch REST API (Phase D3)."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from ...services import ingest_batch_service as batch_svc
from ...services.principal import Principal, current_principal
from ...workers.ingest_batch_worker import mark_batch_cancelled

log = structlog.get_logger(__name__)
# Every route here answered anybody with no token. A batch is an upload in progress: it is
# for a signed-in caller.
router = APIRouter(prefix="/api/ingest/batches", tags=["Ingest Batches"],
                   dependencies=[Depends(current_principal)])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Batch not found")


def _same_company(principal: Principal, batch: dict) -> bool:
    if principal.role == "superadmin":
        return True
    return bool(principal.organization_id) and str(batch.get("organization_id") or "") == str(principal.organization_id)


async def _readable(batch_id: str, principal: Principal) -> dict:
    """The batch, if it is the caller's company's and its conversation is one they may read."""
    from .workflow import _session_guard

    batch = await batch_svc.get_ingest_batch(batch_id)
    if not batch or not _same_company(principal, batch):
        raise _not_found()
    if batch.get("session_id"):
        await _session_guard(str(batch["session_id"]), principal, unknown_ok=True)
    return batch


@router.get("")
async def list_batches(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    principal: Principal = Depends(current_principal),
) -> dict:
    from .workflow import _session_guard

    # The conversation's owner (or their company's admin); a session not yet recorded is still
    # uploading, and its batches are listed only for the caller's own company.
    await _session_guard(session_id, principal, unknown_ok=True)
    batches = [b for b in await batch_svc.list_ingest_batches_for_session(session_id, limit=limit)
               if _same_company(principal, b)]
    return {"session_id": session_id, "batches": batches, "total": len(batches)}


@router.get("/{batch_id}")
async def get_batch(batch_id: str, principal: Principal = Depends(current_principal)) -> dict:
    return await _readable(batch_id, principal)


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: str, principal: Principal = Depends(current_principal)) -> dict:
    await _readable(batch_id, principal)
    mark_batch_cancelled(batch_id)
    batch = await batch_svc.cancel_ingest_batch(batch_id)
    if not batch:
        raise _not_found()
    log.info("ingest_batch.cancelled", batch_id=batch_id)
    return batch
