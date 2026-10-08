"""Maintenance watch (engines/maintenance/watch.py) - the checks Hoist Crons schedules.

    POST /api/maintenance/sla-watch         open work orders past (or near) their SLA
    POST /api/maintenance/ppm-due           maintenance plans overdue or due soon
    POST /api/maintenance/ppm-missed        Missed / Deferred PPM visits not yet rebooked
    POST /api/maintenance/parts-reorder     spare parts at or below their reorder level
    POST /api/maintenance/monthly-summary   last month's WO / SLA / PPM figures by building and vendor

Each reports what it found and, unless raise_approvals=false, puts each finding in the
Approvals queue (source "M") - once, and clears it when a later run no longer finds it. None
creates a work order or a purchase order. A building narrows the check to that building; a
caller allocated to some buildings is narrowed to those.
"""
from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import access
from ...engines.maintenance import watch as watch_svc
from .auth import scope

router = APIRouter(prefix="/api/maintenance", tags=["maintenance-watch"], dependencies=[Depends(scope)])


def _org(s: access.Scope, organization_id: UUID | None) -> UUID:
    org = access.organization_for(s, organization_id)
    if org is None:
        raise HTTPException(status_code=400, detail={
            "ok": False, "reason": "no_organization", "error": "Choose a company to check."})
    return org


def _buildings(s: access.Scope, building_id: UUID | None) -> tuple[UUID, ...] | None:
    if building_id is not None:
        return (access.assert_building(s, building_id, action="check"),)
    return s.building_ids


@router.post("/sla-watch")
async def sla_watch(organization_id: UUID | None = None, building_id: UUID | None = None,
                    horizon_hours: int = Query(24, ge=1, le=168), raise_approvals: bool = True,
                    session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    return await watch_svc.sla_watch(session, organization_id=_org(s, organization_id),
                                     building_ids=_buildings(s, building_id), horizon_hours=horizon_hours,
                                     raise_approvals=raise_approvals)


@router.post("/ppm-due")
async def ppm_due(organization_id: UUID | None = None, building_id: UUID | None = None,
                  days: int = Query(14, ge=1, le=90), raise_approvals: bool = True,
                  session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    return await watch_svc.ppm_due(session, organization_id=_org(s, organization_id),
                                   building_ids=_buildings(s, building_id), days=days, raise_approvals=raise_approvals)


@router.post("/ppm-missed")
async def ppm_missed(organization_id: UUID | None = None, building_id: UUID | None = None,
                     raise_approvals: bool = True,
                     session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    return await watch_svc.ppm_missed(session, organization_id=_org(s, organization_id),
                                      building_ids=_buildings(s, building_id), raise_approvals=raise_approvals)


@router.post("/parts-reorder")
async def parts_reorder(organization_id: UUID | None = None, raise_approvals: bool = True,
                        session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    return await watch_svc.parts_reorder(session, organization_id=_org(s, organization_id),
                                         raise_approvals=raise_approvals)


@router.post("/monthly-summary")
async def monthly_summary(organization_id: UUID | None = None, building_id: UUID | None = None,
                          month: date | None = Query(None, description="Any day in the month; default last month."),
                          raise_approvals: bool = True,
                          session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)) -> dict[str, Any]:
    return await watch_svc.monthly_summary(session, organization_id=_org(s, organization_id),
                                           building_ids=_buildings(s, building_id), month=month,
                                           raise_approvals=raise_approvals)
