"""BE2-07 / BE2-08 / BE2-13 — Journey log endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from typing import List, Optional

from ...core.logging import get_logger
from ...db import get_session
from ...models.journey_log import JourneyLog
from ...api.schemas.journey import JourneyResponse, MilestoneUpdate, JourneyAnalytics, JourneyHealth
from ...services.journey_service import calculate_journey_health
from ...core.exceptions import DatabaseError

router = APIRouter()
log = get_logger(__name__)


async def _get_jlog_or_404(jlog_id: str, session: AsyncSession) -> JourneyLog:
    result = await session.execute(
        select(JourneyLog).where(JourneyLog.jlog_id == jlog_id)
    )
    jlog = result.scalar_one_or_none()
    if not jlog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "journey_not_found", "message": f"Journey {jlog_id!r} not found"},
        )
    return jlog


# ── BE2-13: Analytics (static path — must be before /{jlog_id}) ───────────────

@router.get(
    "/analytics/summary",
    response_model=JourneyAnalytics,
    summary="Journey completion analytics",
    description="Returns completion rates, average duration, and per-milestone rates.",
)
async def journey_analytics(session: AsyncSession = Depends(get_session)):
    log.debug("journey.analytics.start")
    try:
        result = await session.execute(select(JourneyLog))
        journeys: list[JourneyLog] = result.scalars().all()
    except SQLAlchemyError as exc:
        log.error("journey.analytics.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    total = len(journeys)
    completed = sum(1 for j in journeys if j.status == "completed")
    active    = sum(1 for j in journeys if j.status == "active")

    completion_rate = (completed / total) if total else 0.0

    durations = []
    for j in journeys:
        et = j.expected_timeline or {}
        if et.get("duration_hours"):
            durations.append(float(et["duration_hours"]))
    avg_hours = (sum(durations) / len(durations)) if durations else None

    milestone_totals: dict[str, int] = {}
    milestone_done:   dict[str, int] = {}
    for j in journeys:
        for m in (j.milestones or []):
            name = m.get("name", "")
            milestone_totals[name] = milestone_totals.get(name, 0) + 1
            if m.get("status") == "completed":
                milestone_done[name] = milestone_done.get(name, 0) + 1

    milestone_rates = {
        name: round(milestone_done.get(name, 0) / total_count, 3)
        for name, total_count in milestone_totals.items()
        if total_count > 0
    }

    in_progress = sum(1 for j in journeys if j.journey_status == "in_progress")
    failed      = sum(1 for j in journeys if j.journey_status == "failed")

    analytics = JourneyAnalytics(
        total_journeys=total,
        completed=completed,
        active=active,
        in_progress_journeys=in_progress,
        failed_journeys=failed,
        completion_rate=round(completion_rate, 3),
        avg_completion_hours=round(avg_hours, 1) if avg_hours is not None else None,
        milestone_completion_rates=milestone_rates,
    )
    log.debug(
        "journey.analytics.result",
        total=total, completed=completed, active=active,
        completion_rate=analytics.completion_rate,
    )
    return analytics


# ── BE2-07: By work order (compound path — must be before /{jlog_id}) ─────────

@router.get(
    "/by-work-order/{work_order_id}",
    response_model=JourneyResponse,
    summary="Get the journey log for a work order",
)
async def get_journey_by_wo(work_order_id: str, session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == work_order_id)
    )
    jlog = result.scalar_one_or_none()
    if not jlog:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "journey_not_found", "message": f"No journey for work order {work_order_id!r}"},
        )
    return jlog


# ── BE2-07: List / Get ────────────────────────────────────────────────────────

@router.get(
    "/",
    response_model=List[JourneyResponse],
    summary="List journey logs",
    description="Returns journey logs, newest first. Filter by work_order_id or status.",
)
async def list_journeys(
    work_order_id: Optional[str] = Query(None, description="Filter by work order"),
    journey_status: Optional[str] = Query(None, alias="status", description="Filter by journey status"),
    page:  int = Query(1,  ge=1,   description="Page number"),
    limit: int = Query(20, ge=1, le=200, description="Items per page"),
    session: AsyncSession = Depends(get_session),
):
    q = select(JourneyLog)
    if work_order_id:
        q = q.where(JourneyLog.work_order_id == work_order_id)
    if journey_status:
        q = q.where(JourneyLog.status == journey_status)
    q = q.order_by(JourneyLog.created_at.desc()).offset((page - 1) * limit).limit(limit)
    try:
        result = await session.execute(q)
    except SQLAlchemyError as exc:
        raise DatabaseError(str(exc)) from exc
    return result.scalars().all()


@router.get(
    "/{jlog_id}",
    response_model=JourneyResponse,
    summary="Get a journey log by ID",
)
async def get_journey(jlog_id: str, session: AsyncSession = Depends(get_session)):
    return await _get_jlog_or_404(jlog_id, session)


# ── BE2-13: Journey health ────────────────────────────────────────────────────

@router.get(
    "/{jlog_id}/health",
    response_model=JourneyHealth,
    summary="Get health metrics for a journey log",
    description=(
        "Returns completion percentage, cost/time overrun, and overall health status "
        "(on_track | in_progress | at_risk | completed)."
    ),
    responses={404: {"description": "Journey not found"}},
)
async def get_journey_health(jlog_id: str, session: AsyncSession = Depends(get_session)):
    jlog = await _get_jlog_or_404(jlog_id, session)
    health = calculate_journey_health(jlog)
    log.debug(
        "journey.health.result",
        jlog_id=jlog_id,
        health_status=health["health_status"],
        completion=health["completion_percentage"],
    )
    return health


# ── BE2-08: Milestone update ──────────────────────────────────────────────────

@router.patch(
    "/{jlog_id}/milestone",
    response_model=JourneyResponse,
    summary="Update a milestone status in a journey log",
    description=(
        "Sets the `status` of a named milestone. "
        "Valid status values: `pending` | `current` | `completed` | `skipped`."
    ),
)
async def update_milestone(
    jlog_id: str,
    payload: MilestoneUpdate,
    session: AsyncSession = Depends(get_session),
):
    log.info(
        "journey.milestone.update.start",
        jlog_id=jlog_id,
        milestone_name=payload.milestone_name,
        new_status=payload.status,
    )
    jlog = await _get_jlog_or_404(jlog_id, session)

    milestones = list(jlog.milestones or [])
    found = False
    now_iso = datetime.now(timezone.utc).isoformat()

    for i, m in enumerate(milestones):
        if m["name"] == payload.milestone_name:
            milestones[i] = {
                **m,
                "status":    payload.status,
                "timestamp": now_iso if payload.status in ("completed", "current") else m.get("timestamp"),
                "notes":     payload.notes if payload.notes is not None else m.get("notes"),
            }
            found = True
            break

    if not found:
        log.warning(
            "journey.milestone.not_found",
            jlog_id=jlog_id,
            milestone_name=payload.milestone_name,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code":    "milestone_not_found",
                "message": f"Milestone {payload.milestone_name!r} not found in journey {jlog_id!r}",
            },
        )

    jlog.milestones = milestones
    if payload.status == "current":
        jlog.current_step = payload.milestone_name
    jlog.updated_at = datetime.now(timezone.utc)

    try:
        await session.commit()
        await session.refresh(jlog)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("journey.milestone.db_error", jlog_id=jlog_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    log.info(
        "journey.milestone.updated",
        jlog_id=jlog_id,
        work_order_id=jlog.work_order_id,
        milestone_name=payload.milestone_name,
        new_status=payload.status,
    )
    return jlog
