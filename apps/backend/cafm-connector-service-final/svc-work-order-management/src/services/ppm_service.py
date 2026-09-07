"""
Cross-checks whether a PPM schedule exists for an asset and how healthy it is.

Logic:
  - has_ppm=False → recommend creating a schedule
  - status=overdue  → PPM missed, correlate with corrective WO
  - status=due_soon → suggest combining with corrective WO
  - status=ok       → PPM current, no action needed
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ppm_schedule import PPMSchedule
from ..core.logging import get_logger

log = get_logger(__name__)

_DUE_SOON_DAYS = 30


async def check_ppm_for_asset(
    asset_id: Optional[str],
    asset_name: Optional[str],
    session: AsyncSession,
) -> dict:
    """
    Return a PPM status dict for the given asset.
    asset_id is a UUID string (from the resolved asset record).
    asset_name is used only for logging.
    """
    rows: list[PPMSchedule] = []

    if asset_id:
        try:
            aid = UUID(str(asset_id))
            result = await session.execute(
                select(PPMSchedule).where(
                    PPMSchedule.asset_id == aid,
                    PPMSchedule.status == "active",
                )
            )
            rows = result.scalars().all()
        except (ValueError, Exception) as exc:
            log.warning("ppm_service.uuid_parse_error", asset_id=asset_id, exc_info=exc)

    if not rows:
        log.info("ppm_service.no_ppm", asset_name=asset_name)
        return {
            "has_ppm": False,
            "status": "no_ppm",
            "recommendation": (
                "No PPM schedule found for this asset. "
                "Consider creating one to prevent recurring issues."
            ),
        }

    today = datetime.now(timezone.utc).date()
    overdue, due_soon, current = [], [], []

    for ppm in rows:
        if not ppm.next_due_date:
            current.append(ppm)
        elif ppm.next_due_date < today:
            overdue.append(ppm)
        elif (ppm.next_due_date - today).days <= _DUE_SOON_DAYS:
            due_soon.append(ppm)
        else:
            current.append(ppm)

    def _ppm_dict(ppm: PPMSchedule, status: str, recommendation: str) -> dict:
        return {
            "has_ppm":          True,
            "status":           status,
            "ppm_id":           str(ppm.schedule_id),
            "maintenance_type": ppm.maintenance_type,
            "frequency":        ppm.frequency,
            "next_due_date":    ppm.next_due_date.isoformat() if ppm.next_due_date else None,
            "recommendation":   recommendation,
        }

    if overdue:
        ppm = overdue[0]
        days_overdue = (today - ppm.next_due_date).days if ppm.next_due_date else "?"
        log.warning("ppm_service.overdue", asset_name=asset_name, days_overdue=days_overdue)
        return _ppm_dict(
            ppm, "overdue",
            f"PPM overdue by {days_overdue} days (was due {ppm.next_due_date}). "
            "This corrective WO may be related to missed preventive maintenance.",
        )

    if due_soon:
        ppm = due_soon[0]
        days_left = (ppm.next_due_date - today).days if ppm.next_due_date else "?"
        return _ppm_dict(
            ppm, "due_soon",
            f"PPM due in {days_left} days ({ppm.next_due_date}). "
            "Consider combining with this corrective WO to reduce downtime.",
        )

    ppm = current[0]
    return _ppm_dict(ppm, "ok", "PPM is current and on schedule.")
