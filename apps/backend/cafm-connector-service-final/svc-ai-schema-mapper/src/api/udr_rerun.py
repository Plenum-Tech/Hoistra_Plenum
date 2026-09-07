"""Saved UDR script rerun / edit endpoints (Feature 4 backend B.2–B.6).

These endpoints let a saved UDR script edit its sources, columns, or
hierarchy definitions, then rerun a specific phase or reset state to a
prior phase without restarting the entire pipeline.

Edits are persisted into ``MigrationJob.field_mapping_draft`` under a new
``udr_edits`` JSONB key so they survive process restart and are visible to
both the worker and any future graph replay. Rerun / reset endpoints
schedule a worker resume so the LangGraph state machine picks up the patch
on its next step.

Worker contract (svc-ai-schema-mapper-worker):
  * ``resume_migration(migration_id, gate_type, decisions)`` already handles
    gate-based resumes for the existing ``pre-semantic`` / ``field-mapping``
    / ``hierarchy`` / ``write`` gates. The new ``rerun:<phase>`` and
    ``reset:<phase>`` gate types are forwarded the same way; the worker
    interprets them by reading the edits stored in ``field_mapping_draft``
    before invoking the graph from the requested checkpoint.

Spec alignment:
  * B.2 — POST /api/migration/{id}/edit/sources
  * B.3 — POST /api/migration/{id}/edit/columns
  * B.4 — POST /api/migration/{id}/edit/hierarchy
  * B.5 — POST /api/migration/{id}/rerun/{phase}
  * B.6 — POST /api/migration/{id}/reset-to-phase
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger

from ..db import get_async_session_factory
from ..models.migration import MigrationJob

logger = get_logger(__name__)

router = APIRouter(prefix="/api/migration", tags=["UDR rerun"])


# ── Dependency ──────────────────────────────────────────────────────────────


async def get_db_session() -> AsyncSession:
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


# ── Pydantic schemas ────────────────────────────────────────────────────────

RerunPhase = Literal["deterministic", "semantic", "field_mapping", "hierarchy", "validation"]
ResetPhase = Literal[
    "ingest",
    "deterministic",
    "pre_semantic",
    "semantic",
    "field_mapping",
    "hierarchy",
    "validation",
    "final",
]


class SourceFileEdit(BaseModel):
    """A single source file mutation."""

    action: Literal["add", "remove", "replace"]
    filename: str = Field(..., min_length=1, max_length=255)
    data_url: Optional[str] = Field(
        default=None,
        description="Required for add/replace. Blob URL of the new content.",
    )
    replaces: Optional[str] = Field(
        default=None,
        description="Filename being replaced. Required when action == 'replace'.",
    )

    @field_validator("data_url")
    @classmethod
    def _require_url_for_add_replace(cls, v: Optional[str], info: Any) -> Optional[str]:
        action = info.data.get("action")
        if action in {"add", "replace"} and not v:
            raise ValueError("data_url is required when action is add or replace")
        return v


class EditSourcesRequest(BaseModel):
    edits: List[SourceFileEdit] = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=500)


class ColumnEdit(BaseModel):
    table_name: str = Field(..., min_length=1, max_length=64)
    column_name: str = Field(..., min_length=1, max_length=64)
    action: Literal["rename", "map", "create", "remove", "set_type"]
    new_name: Optional[str] = Field(default=None, max_length=64)
    target_field: Optional[str] = Field(default=None, max_length=64)
    data_type: Optional[str] = Field(default=None, max_length=64)
    nullable: Optional[bool] = None


class EditColumnsRequest(BaseModel):
    edits: List[ColumnEdit] = Field(..., min_length=1, max_length=256)
    note: Optional[str] = Field(default=None, max_length=500)


class HierarchyEdit(BaseModel):
    action: Literal["add", "remove", "update"]
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: Optional[str] = Field(default=None, max_length=64)


class EditHierarchyRequest(BaseModel):
    edits: List[HierarchyEdit] = Field(..., min_length=1, max_length=64)
    note: Optional[str] = Field(default=None, max_length=500)


class RerunResponse(BaseModel):
    migration_id: str
    accepted_at: str
    edit_kind: Literal["sources", "columns", "hierarchy", "rerun", "reset"]
    pending_phase: Optional[str] = None
    edits_recorded: int
    queued: bool
    detail: str


class ResetToPhaseRequest(BaseModel):
    target_phase: ResetPhase
    note: Optional[str] = Field(default=None, max_length=500)


# ── Helpers ─────────────────────────────────────────────────────────────────


async def _load_job(session: AsyncSession, migration_id: str) -> MigrationJob:
    """Fetch the MigrationJob row or raise 404."""
    try:
        mid_uuid = UUID(migration_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid migration_id: {exc}") from exc

    result = await session.execute(select(MigrationJob).where(MigrationJob.id == mid_uuid))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Migration not found")
    return job


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_edits(
    draft: Optional[dict[str, Any]],
    bucket: str,
    items: list[dict[str, Any]],
    note: Optional[str],
) -> dict[str, Any]:
    """Append edit records to ``draft.udr_edits.<bucket>`` preserving history."""
    next_draft: dict[str, Any] = dict(draft or {})
    udr = dict(next_draft.get("udr_edits") or {})
    history = list(udr.get(bucket) or [])
    history.append(
        {
            "at": _now_iso(),
            "items": items,
            "note": note,
        }
    )
    udr[bucket] = history
    next_draft["udr_edits"] = udr
    return next_draft


async def _enqueue_resume(
    migration_id: str,
    gate_type: str,
    decisions: dict[str, Any],
) -> bool:
    """Best-effort ARQ enqueue. Returns True on success."""
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        from ..app import get_settings  # type: ignore  # avoid cyclic at import time

        settings = get_settings()
        if not settings.redis_url:
            return False
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        pool = await create_pool(redis_settings)
        try:
            await pool.enqueue_job(
                "resume_migration",
                migration_id=migration_id,
                gate_type=gate_type,
                decisions=decisions,
            )
        finally:
            await pool.aclose()
        logger.info(
            "udr_rerun_enqueued",
            migration_id=migration_id,
            gate_type=gate_type,
        )
        return True
    except Exception as exc:  # pragma: no cover — best effort, logs and falls through
        logger.warning(
            "udr_rerun_enqueue_failed",
            migration_id=migration_id,
            gate_type=gate_type,
            error=str(exc),
        )
        return False


async def _persist_draft(
    session: AsyncSession,
    job: MigrationJob,
    next_draft: dict[str, Any],
) -> None:
    await session.execute(
        update(MigrationJob).where(MigrationJob.id == job.id).values(field_mapping_draft=next_draft)
    )
    await session.commit()


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.post(
    "/{migration_id}/edit/sources",
    response_model=RerunResponse,
    summary="UDR edit: add / remove / replace source files (Feature 4 B.2)",
)
async def edit_sources(
    migration_id: str = Path(..., description="Migration UUID"),
    request: EditSourcesRequest = ...,
    session: AsyncSession = Depends(get_db_session),
) -> RerunResponse:
    job = await _load_job(session, migration_id)
    items = [e.model_dump(exclude_none=True) for e in request.edits]
    next_draft = _merge_edits(job.field_mapping_draft, "sources", items, request.note)
    await _persist_draft(session, job, next_draft)
    return RerunResponse(
        migration_id=migration_id,
        accepted_at=_now_iso(),
        edit_kind="sources",
        edits_recorded=len(items),
        queued=False,
        detail="Source file edits recorded. Call /rerun/{phase} to replay the pipeline with these edits applied.",
    )


@router.post(
    "/{migration_id}/edit/columns",
    response_model=RerunResponse,
    summary="UDR edit: rename / map / create / remove / retype columns (Feature 4 B.3)",
)
async def edit_columns(
    migration_id: str = Path(..., description="Migration UUID"),
    request: EditColumnsRequest = ...,
    session: AsyncSession = Depends(get_db_session),
) -> RerunResponse:
    job = await _load_job(session, migration_id)
    items = [e.model_dump(exclude_none=True) for e in request.edits]
    next_draft = _merge_edits(job.field_mapping_draft, "columns", items, request.note)
    await _persist_draft(session, job, next_draft)
    return RerunResponse(
        migration_id=migration_id,
        accepted_at=_now_iso(),
        edit_kind="columns",
        edits_recorded=len(items),
        queued=False,
        detail="Column edits recorded. Call /rerun/field_mapping or /rerun/semantic to replay mapping with these edits applied.",
    )


@router.post(
    "/{migration_id}/edit/hierarchy",
    response_model=RerunResponse,
    summary="UDR edit: add / update / remove FK relationships (Feature 4 B.4)",
)
async def edit_hierarchy(
    migration_id: str = Path(..., description="Migration UUID"),
    request: EditHierarchyRequest = ...,
    session: AsyncSession = Depends(get_db_session),
) -> RerunResponse:
    job = await _load_job(session, migration_id)
    items = [e.model_dump(exclude_none=True) for e in request.edits]
    next_draft = _merge_edits(job.field_mapping_draft, "hierarchy", items, request.note)
    await _persist_draft(session, job, next_draft)
    return RerunResponse(
        migration_id=migration_id,
        accepted_at=_now_iso(),
        edit_kind="hierarchy",
        edits_recorded=len(items),
        queued=False,
        detail="Hierarchy edits recorded. Call /rerun/hierarchy or /rerun/validation to replay with these edits applied.",
    )


@router.post(
    "/{migration_id}/rerun/{phase}",
    response_model=RerunResponse,
    summary="UDR rerun: replay a specific pipeline phase (Feature 4 B.5)",
)
async def rerun_phase(
    migration_id: str = Path(..., description="Migration UUID"),
    phase: RerunPhase = Path(..., description="Phase to replay from"),
    session: AsyncSession = Depends(get_db_session),
) -> RerunResponse:
    job = await _load_job(session, migration_id)
    next_draft = _merge_edits(
        job.field_mapping_draft,
        "rerun",
        [{"phase": phase, "trigger": "rerun"}],
        None,
    )
    await _persist_draft(session, job, next_draft)

    queued = await _enqueue_resume(
        migration_id,
        gate_type=f"rerun:{phase}",
        decisions={"rerun_phase": phase, "udr_edits": next_draft.get("udr_edits", {})},
    )
    return RerunResponse(
        migration_id=migration_id,
        accepted_at=_now_iso(),
        edit_kind="rerun",
        pending_phase=phase,
        edits_recorded=1,
        queued=queued,
        detail=(
            "Rerun queued — worker will replay the pipeline from the requested phase."
            if queued
            else "Rerun intent persisted. Worker not reachable — call /status once the worker is online to advance."
        ),
    )


@router.post(
    "/{migration_id}/reset-to-phase",
    response_model=RerunResponse,
    summary="UDR reset: rewind state to a prior phase before replaying (Feature 4 B.6)",
)
async def reset_to_phase(
    migration_id: str = Path(..., description="Migration UUID"),
    request: ResetToPhaseRequest = ...,
    session: AsyncSession = Depends(get_db_session),
) -> RerunResponse:
    job = await _load_job(session, migration_id)
    next_draft = _merge_edits(
        job.field_mapping_draft,
        "reset",
        [{"phase": request.target_phase, "trigger": "reset"}],
        request.note,
    )
    await _persist_draft(session, job, next_draft)

    queued = await _enqueue_resume(
        migration_id,
        gate_type=f"reset:{request.target_phase}",
        decisions={"reset_phase": request.target_phase, "udr_edits": next_draft.get("udr_edits", {})},
    )
    return RerunResponse(
        migration_id=migration_id,
        accepted_at=_now_iso(),
        edit_kind="reset",
        pending_phase=request.target_phase,
        edits_recorded=1,
        queued=queued,
        detail=(
            "Reset queued — worker will rewind state to the requested phase."
            if queued
            else "Reset intent persisted. Worker not reachable — call /status once the worker is online to advance."
        ),
    )
