"""Bulk ingest batch REST API (Phase D3)."""
from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query

from ...services import ingest_batch_service as batch_svc
from ...workers.ingest_batch_worker import mark_batch_cancelled

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/ingest/batches", tags=["Ingest Batches"])


@router.get("")
async def list_batches(
    session_id: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    batches = await batch_svc.list_ingest_batches_for_session(session_id, limit=limit)
    return {"session_id": session_id, "batches": batches, "total": len(batches)}


@router.get("/{batch_id}")
async def get_batch(batch_id: str) -> dict:
    batch = await batch_svc.get_ingest_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.post("/{batch_id}/cancel")
async def cancel_batch(batch_id: str) -> dict:
    mark_batch_cancelled(batch_id)
    batch = await batch_svc.cancel_ingest_batch(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    log.info("ingest_batch.cancelled", batch_id=batch_id)
    return batch
