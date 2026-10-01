"""The Home page's money cards, read live — the value ledger and the P&L."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines import value_ledger as value_svc
from ...engines import value_savings as savings_svc
from ...engines.auth import access
from .auth import scope

router = APIRouter(prefix="/api/value", tags=["value"],
                   # Signed-in callers only, own company only — the same boundary as
                   # every other domain router.
                   dependencies=[Depends(scope)])


@router.get("/summary")
async def value_summary(
    organization_id: UUID | None = Query(None),
    year: int | None = Query(None, ge=2000, le=2100,
                             description="Calendar year; defaults to the current one."),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """The Platform value ledger and the P&L actuals in one read.

    Both Home cards' figures, derived from the store — anomalies, invoice lines,
    variance alerts, recommendations, meter readings — never from a constant. A module
    whose engines record no priced value says so; budgets are null until a budget
    ledger exists. Same company and building narrowing as every other read:
    engines/value_ledger.py states what each figure means and what is deliberately
    not claimed."""
    org_id = access.organization_for(s, organization_id)
    return await value_svc.read_value_summary(
        session,
        organization_id=org_id,
        building_ids=s.building_ids,
        year=year,
    )


@router.get("/savings")
async def savings(
    organization_id: UUID | None = Query(None),
    building_id: UUID | None = Query(None, description="One building; omit for every building the caller may see."),
    period: str | None = Query(None, max_length=20,
                               description="last_month (default), this_month, this_year, last_90_days."),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Where money can be saved, and the work that captures it - the chat's cost-saving read.

    The value ledger (detected vs saved), open jobs with money at stake, repeat-failure assets
    against their replacement value, invoice overcharges, reactive vs planned spend, and the
    figures to check before quoting them. Reads only (engines/value_savings.py)."""
    org_id = access.organization_for(s, organization_id)
    ids = (access.assert_building(s, building_id, action="read"),) if building_id else s.building_ids
    return await savings_svc.read_savings(session, organization_id=org_id, building_ids=ids, period=period)
