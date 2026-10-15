"""GET /api/assets/replacement-candidates - parts to reorder and assets at the end of their life
(engines/replacement_candidates.py). Signed in; narrowed to the caller's company and buildings."""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines import replacement_candidates as svc
from ...engines.auth import access
from .auth import scope

router = APIRouter(prefix="/api/assets", tags=["assets"], dependencies=[Depends(scope)])


@router.get("/replacement-candidates")
async def replacement_candidates(
    building_id: UUID | None = Query(None),
    period_from: date | None = Query(None, description="Repeat failures counted from this date (default: a year back)."),
    period_to: date | None = Query(None, description="...and before this date."),
    organization_id: UUID | None = Query(None),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
) -> dict[str, Any]:
    org = access.organization_for(s, organization_id)
    if building_id is not None:
        access.assert_building(s, building_id, action="read")
    return await svc.read_candidates(session, organization_id=org, building_ids=s.building_ids, building_id=building_id,
                                     period_from=period_from, period_to=period_to)
