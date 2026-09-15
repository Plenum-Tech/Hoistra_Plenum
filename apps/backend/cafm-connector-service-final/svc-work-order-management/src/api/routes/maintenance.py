"""The Maintenance module's endpoints: decisions owed, inspection reports, PPM health.

Three reads the shell's Maintenance screen needs and nothing served. They live here rather
than in operations-intelligence because work orders are the spine of all three and this is
the service that owns them, already narrowed to the caller's buildings on every route.

Each is building-scoped. Passing a building narrows to it and is refused if the caller is
not allocated to it — refused rather than answered empty, so "you may not see this" is never
confused with "there is nothing here".
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...db import get_session
from ...services import maintenance as mx
from ...services.principal import Principal, assert_building, current_principal

router = APIRouter()
log = get_logger(__name__)


def _scope(principal: Principal, building_id: Optional[str]) -> list[UUID] | None:
    """The buildings this request covers: one named building, checked, or the caller's own.

    None means unrestricted — an admin over the whole company. An empty list means allocated
    to nothing, which the engines turn into a predicate matching no row.
    """
    if building_id:
        assert_building(principal, building_id, action="read")
        return [UUID(str(building_id))]
    if principal.building_ids is None:
        return None
    return list(principal.building_ids)


@router.get(
    "/decisions",
    summary="Decisions the property manager owes",
    description=(
        "Work orders that are blocked, awaiting approval or running past their due date, "
        "plus the orders that do not exist yet because another module says they should. "
        "The second kind has a null work_order and the state 'To raise'."
    ),
    tags=["Maintenance"],
)
async def list_decisions(
    building_id: Optional[str] = Query(None, description="Narrow to one building"),
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    ids = _scope(principal, building_id)
    out = await mx.decisions(session, building_ids=ids, limit=limit)
    log.debug("maintenance.decisions", count=out.get("count"), building_id=building_id)
    return out


@router.get(
    "/inspections",
    summary="Inspection reports on completed work",
    description=(
        "Reports with their finding, risk level and whether a corrective action is still "
        "open. Reports carry no building of their own and reach one through the asset they "
        "are about; a report whose asset cannot be placed is not returned."
    ),
    tags=["Maintenance"],
)
async def list_inspections(
    building_id: Optional[str] = Query(None, description="Narrow to one building"),
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    ids = _scope(principal, building_id)
    out = await mx.inspections(session, building_ids=ids, limit=limit)
    log.debug("maintenance.inspections", count=out.get("count"), building_id=building_id)
    return out


@router.get(
    "/ppm",
    summary="Planned maintenance health per vendor",
    description=(
        "Planned against done, missed and late, with the next visit due, derived from the "
        "work orders themselves. A planned order that is finished counts as done; one past "
        "its due date and unfinished is missed; one finished after its due date is late."
    ),
    tags=["Maintenance"],
)
async def ppm_health(
    building_id: Optional[str] = Query(None, description="Narrow to one building"),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    ids = _scope(principal, building_id)
    out = await mx.ppm_health(session, building_ids=ids)
    log.debug("maintenance.ppm", contracts=len(out.get("contracts") or []), building_id=building_id)
    return out


@router.get(
    "/summary",
    summary="The Maintenance module in one call",
    description="Decisions, inspections and PPM health together, for the screen's first paint.",
    tags=["Maintenance"],
)
async def summary(
    building_id: Optional[str] = Query(None, description="Narrow to one building"),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    ids = _scope(principal, building_id)
    dec = await mx.decisions(session, building_ids=ids, limit=200)
    ins = await mx.inspections(session, building_ids=ids, limit=200)
    ppm = await mx.ppm_health(session, building_ids=ids)
    return {
        "ok": True,
        "building_id": building_id,
        "decisions": {"count": dec.get("count", 0), "by_state": dec.get("by_state", {})},
        "inspections": {
            "count": ins.get("count", 0),
            "recommendations_open": ins.get("recommendations_open", 0),
            "by_risk": ins.get("by_risk", {}),
        },
        "ppm": ppm.get("summary", {}),
    }
