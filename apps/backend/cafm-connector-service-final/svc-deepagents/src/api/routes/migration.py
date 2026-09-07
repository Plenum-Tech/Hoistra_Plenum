"""
Migration router — convenience REST endpoints wrapping the Schema Mapper pipeline
(no LLM orchestration). Use these when the frontend needs structured migration
lifecycle management without a chat interface.

The Schema Mapper runs an async 9-node LangGraph pipeline with 3 HITL gates.
Typical REST flow:
  POST /api/migration/start        → migration_id (multipart upload)
  GET  /api/migration/{id}/status  → poll until complete / awaiting_review
  POST /api/migration/{id}/gate/pre-semantic   → Gate 0 decisions
  POST /api/migration/{id}/gate/field-mapping  → Gate 1 decisions
  POST /api/migration/{id}/gate/hierarchy      → Gate 2 decisions
  POST /api/migration/{id}/gate/final          → Gate 3 confirmation
  POST /api/migration/{id}/advance             → advance non-gate step pause
  GET  /api/migration/{id}/mappings            → audit trail (after complete)
  GET  /api/migration                          → list all runs
"""
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...agents.migration_agent import (
    start_migration,
    get_migration_status,
    submit_pre_semantic,
    submit_field_mapping,
    submit_hierarchy,
    get_migration_mappings,
    list_migrations,
    _get,
    _post,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/migration", tags=["Migration"])


def _raise_if_error(result: dict) -> dict:
    if isinstance(result, dict) and "error" in result:
        status_code = result.get("status_code", 400)
        raise HTTPException(status_code=status_code, detail=result["error"])
    return result


# ── Start a migration via file upload ──────────────────────────────────────

class StartMigrationForm(BaseModel):
    file_path: str
    cmms_name: str = "Custom"
    organization_id: str = "00000000-0000-0000-0000-000000000001"


@router.post("/start")
async def start(body: StartMigrationForm) -> dict:
    """Start the schema mapper pipeline by providing a local file path.

    The file must be accessible to the service process. For browser-based
    uploads use the Schema Mapper UI at port 8003 directly.
    """
    result = await start_migration.ainvoke({
        "file_path": body.file_path,
        "cmms_name": body.cmms_name,
        "organization_id": body.organization_id,
    })
    return _raise_if_error(result)


# ── Status polling ─────────────────────────────────────────────────────────

@router.get("/status/{migration_id}")
async def status(migration_id: str) -> dict:
    """Poll the current status of a migration run.

    Returns status, progress_pct, current_step, and — when status ==
    'awaiting_review' — pending_gate_type and pending_gate_payload.
    """
    result = await get_migration_status.ainvoke({"migration_id": migration_id})
    return _raise_if_error(result)


# ── Advance non-gate step pause ────────────────────────────────────────────

@router.post("/advance/{migration_id}")
async def advance(migration_id: str) -> dict:
    """Advance the pipeline past a step_paused state (non-gate nodes)."""
    try:
        return await _post(f"/api/migration/{migration_id}/advance")
    except Exception as exc:
        return _raise_if_error(_err_dict(exc))


# ── HITL gates ─────────────────────────────────────────────────────────────

class PreSemanticRequest(BaseModel):
    approve_all: bool = True
    decisions: dict | None = None


class FieldMappingRequest(BaseModel):
    approve_all: bool = True
    flagged_decisions: dict | None = None
    unmapped_decisions: dict | None = None


class HierarchyRequest(BaseModel):
    approve_all: bool = True
    approved_hierarchies: list | None = None
    corrections: list | None = None


class FinalRequest(BaseModel):
    confirmed: bool = True


@router.post("/gate/pre-semantic/{migration_id}")
async def gate_pre_semantic(migration_id: str, body: PreSemanticRequest) -> dict:
    """Respond to Gate 0 — T1 mapping review before semantic phase."""
    result = await submit_pre_semantic.ainvoke({
        "migration_id": migration_id,
        "approve_all": body.approve_all,
        "decisions": body.decisions,
    })
    return _raise_if_error(result)


@router.post("/gate/field-mapping/{migration_id}")
async def gate_field_mapping(migration_id: str, body: FieldMappingRequest) -> dict:
    """Respond to Gate 1 — field mapping approval (low-confidence mappings)."""
    result = await submit_field_mapping.ainvoke({
        "migration_id": migration_id,
        "approve_all": body.approve_all,
        "flagged_decisions": body.flagged_decisions,
        "unmapped_decisions": body.unmapped_decisions,
    })
    return _raise_if_error(result)


@router.post("/gate/hierarchy/{migration_id}")
async def gate_hierarchy(migration_id: str, body: HierarchyRequest) -> dict:
    """Respond to Gate 2 — hierarchy verification (FK relationships)."""
    result = await submit_hierarchy.ainvoke({
        "migration_id": migration_id,
        "approve_all": body.approve_all,
        "approved_hierarchies": body.approved_hierarchies,
        "corrections": body.corrections,
    })
    return _raise_if_error(result)


@router.post("/gate/final/{migration_id}")
async def gate_final(migration_id: str, body: FinalRequest) -> dict:
    """Respond to Gate 3 — final confirmation before DB handoff."""
    try:
        return await _post(
            f"/api/migration/{migration_id}/gate/final",
            json={"confirmed": body.confirmed},
        )
    except Exception as exc:
        return _raise_if_error(_err_dict(exc))


# ── Post-migration queries ─────────────────────────────────────────────────

@router.get("/mappings/{migration_id}")
async def mappings(migration_id: str) -> dict:
    """Get the complete field mapping audit trail for a finished migration."""
    result = await get_migration_mappings.ainvoke({"migration_id": migration_id})
    return _raise_if_error(result)


@router.get("")
async def list_all(organization_id: str = "00000000-0000-0000-0000-000000000001") -> dict:
    """List all migration runs for an organization."""
    result = await list_migrations.ainvoke({"organization_id": organization_id})
    return _raise_if_error(result)


# ── Internal helper ────────────────────────────────────────────────────────

def _err_dict(exc: Exception) -> dict:
    import httpx as _httpx
    if isinstance(exc, _httpx.HTTPStatusError):
        return {"error": exc.response.text[:300], "status_code": exc.response.status_code}
    return {"error": str(exc)[:300]}
