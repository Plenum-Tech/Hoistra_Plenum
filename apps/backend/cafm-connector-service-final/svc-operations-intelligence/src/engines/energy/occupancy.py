"""Site occupancy change log — gates baseline-drift anomalies."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import SiteOccupancyLog
from ...shared.approvals import write_audit

log = get_logger(__name__)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def log_occupancy_change(
    session: AsyncSession,
    *,
    site_id: UUID,
    occupancy_state: str,
    changed_at: datetime | None = None,
    notes: str | None = None,
    organization_id: UUID | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    row = SiteOccupancyLog(
        id=uuid4(),
        organization_id=organization_id,
        site_id=site_id,
        occupancy_state=occupancy_state,
        changed_at=_aware(changed_at or datetime.now(timezone.utc)),
        notes=notes,
        source=source,
    )
    session.add(row)
    await write_audit(
        session,
        actor="pm",
        action_type="energy.occupancy.log",
        source_feature="C",
        organization_id=organization_id,
        detail={"site_id": str(site_id), "state": occupancy_state},
    )
    await session.commit()
    return {
        "ok": True,
        "id": str(row.id),
        "site_id": str(site_id),
        "occupancy_state": occupancy_state,
        "changed_at": row.changed_at.isoformat(),
    }


async def has_occupancy_change(
    session: AsyncSession,
    *,
    site_id: UUID | None,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """True if any occupancy log falls in [window_start, window_end]."""
    if not site_id:
        return False
    q = (
        select(SiteOccupancyLog.id)
        .where(
            SiteOccupancyLog.site_id == site_id,
            SiteOccupancyLog.changed_at >= _aware(window_start),
            SiteOccupancyLog.changed_at <= _aware(window_end),
        )
        .limit(1)
    )
    hit = (await session.execute(q)).scalar_one_or_none()
    return hit is not None
