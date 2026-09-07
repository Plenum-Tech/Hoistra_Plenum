"""BE2-04 / BE2-11 — Asset and location lookup endpoints."""
import uuid as uuid_module

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional

from ...core.logging import get_logger
from ...db import get_session
from ...models.asset import Asset
from ...models.location import Location
from ...api.schemas.asset import AssetResponse, LocationResponse
from ...core.exceptions import DatabaseError

router = APIRouter()
log = get_logger(__name__)


# ── BE2-04: Assets ────────────────────────────────────────────────────────────

@router.get(
    "/assets",
    response_model=List[AssetResponse],
    summary="List assets",
    description="Search and filter assets. All query params are optional.",
    tags=["Assets"],
)
async def list_assets(
    q:          Optional[str]  = Query(None, description="Name substring search (case-insensitive)"),
    asset_type: Optional[str]  = Query(None, description="Ignored — category is a UUID FK in plenum_cafm.assets"),
    location:   Optional[str]  = Query(None, description="Ignored — location is a UUID FK in plenum_cafm.assets"),
    active:     Optional[bool] = Query(None, description="Ignored — plenum_cafm.assets uses a status string, not a boolean"),
    page:       int            = Query(1, ge=1),
    limit:      int            = Query(50, ge=1, le=200),
    session:    AsyncSession   = Depends(get_session),
):
    query = select(Asset)
    # No active/status filter — the real table uses a status string whose exact
    # values depend on the import source. Return all assets for dropdown use.
    if q:
        query = query.where(Asset.asset_name.ilike(f"%{q}%"))
    query = query.order_by(Asset.asset_name).offset((page - 1) * limit).limit(limit)

    try:
        result = await session.execute(query)
    except SQLAlchemyError as exc:
        log.error("assets.list.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    rows = result.scalars().all()
    log.debug("assets.list.result", count=len(rows), q=q)
    return rows


@router.get(
    "/assets/{asset_id}",
    response_model=AssetResponse,
    summary="Get an asset by ID",
    responses={404: {"description": "Asset not found"}},
    tags=["Assets"],
)
async def get_asset(asset_id: str, session: AsyncSession = Depends(get_session)):
    try:
        asset_uuid = uuid_module.UUID(asset_id)
    except ValueError:
        log.warning("assets.get.invalid_uuid", asset_id=asset_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "asset_not_found", "message": f"Asset {asset_id!r} not found"},
        )
    try:
        result = await session.execute(select(Asset).where(Asset.asset_id == asset_uuid))
    except SQLAlchemyError as exc:
        log.error("assets.get.db_error", asset_id=asset_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    asset = result.scalar_one_or_none()
    if not asset:
        log.warning("assets.get.not_found", asset_id=asset_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "asset_not_found", "message": f"Asset {asset_id!r} not found"},
        )
    log.debug("assets.get.found", asset_id=asset_id, asset_name=asset.asset_name)
    return asset


# ── BE2-11: Locations ─────────────────────────────────────────────────────────

@router.get(
    "/locations",
    response_model=List[LocationResponse],
    summary="List locations for form dropdowns",
    tags=["Locations"],
)
async def list_locations(
    q:       Optional[str]  = Query(None, description="Name substring search"),
    active:  Optional[bool] = Query(True, description="Ignored — real table has no active flag"),
    page:    int            = Query(1, ge=1),
    limit:   int            = Query(100, ge=1, le=500),
    session: AsyncSession   = Depends(get_session),
):
    # Real plenum_cafm.locations has no 'active' column — return all
    query = select(Location)
    if q:
        query = query.where(Location.name.ilike(f"%{q}%"))
    query = query.order_by(Location.name).offset((page - 1) * limit).limit(limit)

    try:
        result = await session.execute(query)
    except SQLAlchemyError as exc:
        raise DatabaseError(str(exc)) from exc
    rows = result.scalars().all()
    log.debug("locations.list.result", count=len(rows), q=q, page=page, limit=limit)
    return rows
