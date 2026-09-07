"""Orchestrator tools for bulk ingest batch status."""
from __future__ import annotations

from langchain_core.tools import tool

from ..services import ingest_batch_service as batch_svc
from .meta_tools import get_session_context


@tool
async def get_ingest_batch_status(batch_id: str) -> dict:
    """Get progress for a bulk file ingestion batch (per-file status and counts).

    Use after uploading more than a few files when the API returns a batch_id.
    """
    if not batch_id:
        return {"error": "batch_id is required"}
    batch = await batch_svc.get_ingest_batch(batch_id)
    if not batch:
        return {"error": f"Batch not found: {batch_id}"}
    return batch


@tool
async def list_session_ingest_batches(limit: int = 10) -> dict:
    """List recent bulk ingestion batches for the current chat session."""
    session_id = get_session_context()
    if not session_id or session_id == "shared":
        return {"error": "No active session context for batch listing"}
    jobs = await batch_svc.list_ingest_batches_for_session(session_id, limit=limit)
    return {"session_id": session_id, "batches": jobs, "total": len(jobs)}
