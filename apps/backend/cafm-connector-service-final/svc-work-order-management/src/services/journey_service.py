"""BE2-06 — Auto-create journey log when a work order is created."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.journey_log import JourneyLog
from ..models.status_history import StatusHistory
from ..core.logging import get_logger

log = get_logger(__name__)

_PRIORITY_HOURS: dict[str, int] = {
    "critical": 4,
    "urgent":   24,
    "high":     48,
    "medium":   72,
    "low":      120,
}

_MILESTONE_SEQUENCE = [
    "pending_approval",
    "preparing",
    "prepared",
    "active",
    "completed",
    "closed",
]


def _jlog_id() -> str:
    return f"JL-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"


def _history_id() -> str:
    return f"SH-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"


def _build_milestones(initial_status: str = "pending_approval") -> list:
    milestones = []
    for name in _MILESTONE_SEQUENCE:
        if name == initial_status:
            milestones.append({"name": name, "status": "current", "timestamp": None, "notes": None})
        else:
            milestones.append({"name": name, "status": "pending", "timestamp": None, "notes": None})
    return milestones


def _build_expected_timeline(priority: str) -> dict:
    hours = _PRIORITY_HOURS.get(priority, 72)
    now = datetime.now(timezone.utc)
    return {
        "start":        now.isoformat(),
        "expected_end": (now + timedelta(hours=hours)).isoformat(),
        "duration_hours": hours,
    }


async def create_journey_for_work_order(
    work_order_id: str,
    priority: str,
    session: AsyncSession,
    asset_id: Optional[str] = None,
    source_system: Optional[str] = None,
    assigned_technician_id: Optional[str] = None,
    assigned_technician_name: Optional[str] = None,
    estimated_cost: Optional[float] = None,
) -> JourneyLog:
    """Create a journey log for a newly created work order."""
    hours = _PRIORITY_HOURS.get(priority, 72)
    jlog_id = _jlog_id()
    jlog = JourneyLog(
        jlog_id=jlog_id,
        work_order_id=work_order_id,
        status="active",
        journey_status="in_progress",
        milestones=_build_milestones("pending_approval"),
        expected_timeline=_build_expected_timeline(priority),
        current_step="pending_approval",
        completed=False,
        asset_id=asset_id,
        source_system=source_system or "api",
        assigned_technician_id=assigned_technician_id,
        assigned_technician_name=assigned_technician_name,
        estimated_cost=estimated_cost,
        estimated_duration_hours=hours,
        status_change_history={
            datetime.now(timezone.utc).isoformat(): {
                "old_status": None,
                "new_status": "in_progress",
            }
        },
    )
    session.add(jlog)
    log.info(
        "journey.created",
        jlog_id=jlog_id,
        work_order_id=work_order_id,
        priority=priority,
        sla_hours=hours,
        asset_id=asset_id,
    )
    return jlog


async def record_status_change(
    work_order_id: str,
    from_status: str | None,
    to_status: str,
    session: AsyncSession,
    changed_by: str = "system",
    notes: str | None = None,
) -> StatusHistory:
    """Append a row to wo_status_history for every status transition."""
    entry = StatusHistory(
        history_id=_history_id(),
        work_order_id=work_order_id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        notes=notes,
        changed_at=datetime.now(timezone.utc),
    )
    session.add(entry)
    log.info(
        "journey.status_change",
        work_order_id=work_order_id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
    )
    return entry


async def advance_journey_milestone(
    work_order_id: str,
    new_status: str,
    session: AsyncSession,
) -> None:
    """Mark the reached milestone as completed and the next as current.

    Sets actual_start when status first becomes 'active' and actual_end
    (+ computes actual_duration_hours) when status reaches 'completed'/'closed'.
    """
    result = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == work_order_id)
    )
    jlog = result.scalar_one_or_none()
    if not jlog or not jlog.milestones:
        log.warning("journey.advance.no_journey", work_order_id=work_order_id)
        return

    milestones = list(jlog.milestones)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    for i, m in enumerate(milestones):
        if m["name"] == new_status:
            milestones[i] = {**m, "status": "current", "timestamp": now_iso}
        elif m["status"] == "current" and m["name"] != new_status:
            milestones[i] = {**m, "status": "completed", "timestamp": now_iso}

    if new_status in ("completed", "closed"):
        jlog.status = "completed"
        jlog.journey_status = "completed"
        jlog.completed = True
        if not jlog.actual_end:
            jlog.actual_end = now
            if jlog.actual_start:
                jlog.actual_duration_hours = int(
                    (now - jlog.actual_start).total_seconds() / 3600
                )
    elif new_status == "active" and not jlog.actual_start:
        jlog.actual_start = now

    jlog.milestones = milestones
    jlog.current_step = new_status
    jlog.updated_at = now

    log.info(
        "journey.milestone_advanced",
        work_order_id=work_order_id,
        jlog_id=jlog.jlog_id,
        new_step=new_status,
        journey_complete=new_status in ("completed", "closed"),
    )


async def update_journey_status(
    work_order_id: str,
    new_status: str,
    session: AsyncSession,
    notes: Optional[str] = None,
    changed_by: str = "system",
) -> Optional[JourneyLog]:
    """Update journey_status with a full audit trail in status_change_history."""
    result = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == work_order_id)
    )
    jlog = result.scalar_one_or_none()
    if not jlog:
        return None

    old_status = jlog.journey_status
    now = datetime.now(timezone.utc)

    jlog.journey_status = new_status
    history = dict(jlog.status_change_history or {})
    history[now.isoformat()] = {
        "old_status":  old_status,
        "new_status":  new_status,
        "timestamp":   now.isoformat(),
        "changed_by":  changed_by,
        "notes":       notes,
    }
    jlog.status_change_history = history

    if new_status == "in_progress" and not jlog.actual_start:
        jlog.actual_start = now
    if new_status in ("completed", "closed") and not jlog.actual_end:
        jlog.actual_end = now
        if jlog.actual_start:
            jlog.actual_duration_hours = int(
                (now - jlog.actual_start).total_seconds() / 3600
            )

    jlog.updated_at = now
    log.info(
        "journey.status_updated",
        work_order_id=work_order_id,
        old_status=old_status,
        new_status=new_status,
    )
    return jlog


async def complete_milestone(
    work_order_id: str,
    milestone_name: str,
    session: AsyncSession,
    notes: Optional[str] = None,
) -> None:
    """Mark a named milestone as completed and record it in milestone_history."""
    result = await session.execute(
        select(JourneyLog).where(JourneyLog.work_order_id == work_order_id)
    )
    jlog = result.scalar_one_or_none()
    if not jlog:
        return

    jlog.update_milestone(milestone_name, "completed")

    now_iso = datetime.now(timezone.utc).isoformat()
    history = dict(jlog.milestone_history or {})
    history[f"{milestone_name}_{now_iso}"] = {
        "status":    "completed",
        "timestamp": now_iso,
        "notes":     notes,
    }
    jlog.milestone_history = history
    jlog.updated_at = datetime.now(timezone.utc)
    log.info(
        "journey.milestone_completed",
        work_order_id=work_order_id,
        milestone_name=milestone_name,
    )


def calculate_journey_health(jlog: JourneyLog) -> dict:
    """BE2-13: Calculate health metrics for a journey log."""
    completion = jlog.get_completion_percentage()

    if completion == 100:
        health_status = "completed"
    elif completion >= 75:
        health_status = "on_track"
    elif completion >= 50:
        health_status = "in_progress"
    else:
        health_status = "at_risk"

    time_overrun = 0
    if jlog.estimated_duration_hours is not None and jlog.actual_duration_hours is not None:
        time_overrun = max(0, jlog.actual_duration_hours - jlog.estimated_duration_hours)

    cost_overrun = 0.0
    if jlog.estimated_cost is not None and jlog.actual_cost is not None:
        cost_overrun = float(jlog.actual_cost) - float(jlog.estimated_cost)

    return {
        "health_status":        health_status,
        "completion_percentage": completion,
        "time_overrun_hours":   time_overrun,
        "cost_overrun":         cost_overrun,
        "on_track":             health_status in ("on_track", "completed"),
        "requires_attention":   health_status == "at_risk",
    }
