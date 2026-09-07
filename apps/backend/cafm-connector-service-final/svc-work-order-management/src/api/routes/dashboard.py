"""BE2-10 — Dashboard statistics endpoint."""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from datetime import date

from ...core.logging import get_logger
from ...db import get_session
from ...models.work_order import WorkOrder
from ...models.asset import Asset
from ...api.schemas.journey import DashboardStats
from ...core.exceptions import DatabaseError

router = APIRouter()
log = get_logger(__name__)


@router.get(
    "/stats",
    response_model=DashboardStats,
    summary="Dashboard statistics",
    description=(
        "Returns aggregate counts: total work orders, counts by status, "
        "counts by priority, counts by source, created today, and assets by category."
    ),
)
async def get_dashboard_stats(session: AsyncSession = Depends(get_session)):
    log.debug("dashboard.stats.start")
    try:
        wo_result = await session.execute(
            select(WorkOrder).where(WorkOrder.work_order_id.isnot(None))
        )
        work_orders: list[WorkOrder] = wo_result.scalars().all()
    except SQLAlchemyError as exc:
        log.error("dashboard.stats.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    today = date.today()
    by_status:   dict[str, int] = {}
    by_priority: dict[str, int] = {}
    by_source:   dict[str, int] = {}
    created_today = 0

    for wo in work_orders:
        by_status[wo.status]     = by_status.get(wo.status, 0) + 1
        by_priority[wo.priority] = by_priority.get(wo.priority, 0) + 1
        by_source[wo.source]     = by_source.get(wo.source, 0) + 1
        if wo.created_at and hasattr(wo.created_at, "date") and wo.created_at.date() == today:
            created_today += 1

    # assets_by_category — group by asset category name where available
    assets_by_category: dict[str, int] = {}
    try:
        asset_result = await session.execute(select(Asset))
        for asset in asset_result.scalars().all():
            category = getattr(asset, "category", None) or getattr(asset, "asset_type", None) or "Unknown"
            assets_by_category[category] = assets_by_category.get(category, 0) + 1
    except Exception:
        pass  # non-critical; leave as empty dict if assets table unavailable

    stats = DashboardStats(
        total=len(work_orders),
        by_status=by_status,
        by_priority=by_priority,
        by_source=by_source,
        created_today=created_today,
        assets_by_category=assets_by_category,
    )
    log.debug(
        "dashboard.stats.result",
        total=stats.total,
        created_today=stats.created_today,
        by_status=stats.by_status,
        asset_categories=len(assets_by_category),
    )
    return stats
