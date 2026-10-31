"""GET /api/work-orders/blockers - the open work orders that cannot proceed because of their vendor
(engines/work_order_blockers.py). Signed in; narrowed to the caller's company and buildings."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines import work_order_blockers as svc
from ...engines.auth import access
from .auth import scope

router = APIRouter(prefix="/api/work-orders", tags=["work-orders"], dependencies=[Depends(scope)])


@router.get("/blockers")
async def work_order_blockers(
    building_id: UUID | None = Query(None),
    period_from: date | None = Query(None, description="Work orders raised on or after this date."),
    period_to: date | None = Query(None, description="...and before this date."),
    wo_code: list[str] | None = Query(None, description="Only these work orders."),
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    org = access.organization_for(s, organization_id)
    if building_id is not None:
        access.assert_building(s, building_id, action="read")
    return await svc.read_blockers(session, organization_id=org, building_ids=s.building_ids, building_id=building_id,
                                   period_from=period_from, period_to=period_to, wo_codes=wo_code)
