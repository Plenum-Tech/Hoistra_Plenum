"""BE2-04 / BE2-11 — Asset and location lookup endpoints."""
import uuid as uuid_module

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional

from ...core.logging import get_logger
from ...db import get_session
from ...models.asset import Asset
from ...models.location import Location
from ...api.schemas.asset import AssetCategoryResponse, AssetResponse, LocationResponse
from ...core.exceptions import DatabaseError
from ...services import asset_catalogue
from ...services.principal import Principal, assert_building, current_principal, scope_select

router = APIRouter()
log = get_logger(__name__)


# ── BE2-04: Assets ────────────────────────────────────────────────────────────

@router.get(
    "/assets",
    response_model=List[AssetResponse],
    summary="List assets across the caller's whole portfolio",
    description=(
        "Every asset the caller may see, across all their buildings. No building_id is "
        "required; pass one to narrow to a single building. The total before paging is "
        "returned in the X-Total-Count header."
    ),
    tags=["Assets"],
)
async def list_assets(
    response:    Response,
    q:           Optional[str]  = Query(None, description="Substring of the name, code or serial number"),
    building_id: Optional[str]  = Query(None, description="Narrow to one building"),
    category_id: Optional[str]  = Query(None, description="Narrow to one asset category"),
    criticality: Optional[str]  = Query(None, description="Exact criticality value as stored"),
    status:      Optional[str]  = Query(None, description="Exact status value as stored"),
    asset_type:  Optional[str]  = Query(None, description="Ignored — category is a UUID FK in plenum_cafm.assets"),
    location:    Optional[str]  = Query(None, description="Ignored — location is a UUID FK; use location_id"),
    active:      Optional[bool] = Query(None, description="Ignored — plenum_cafm.assets uses a status string, not a boolean"),
    page:        int            = Query(1, ge=1),
    limit:       int            = Query(50, ge=1, le=200),
    session:     AsyncSession   = Depends(get_session),
    principal: Principal = Depends(current_principal),
):
    query = scope_select(select(Asset), principal, Asset.building_id)
    if building_id:
        # Asked for one building: 403 rather than an empty list, so a caller who is not
        # allocated to it learns that, instead of concluding the building has no assets.
        assert_building(principal, building_id)
        query = query.where(Asset.building_id == building_id)
    if q:
        like = f"%{q}%"
        query = query.where(or_(Asset.asset_name.ilike(like),
                                Asset.asset_code.ilike(like),
                                Asset.serial_number.ilike(like)))
    if category_id:
        query = query.where(cast(Asset.category_id, String) == str(category_id))
    if criticality:
        query = query.where(Asset.criticality == criticality)
    if status:
        query = query.where(Asset.status == status)

    try:
        total = (await session.execute(
            select(func.count()).select_from(query.subquery()))).scalar_one()
        rows = (await session.execute(
            query.order_by(Asset.asset_name).offset((page - 1) * limit).limit(limit)
        )).scalars().all()
    except SQLAlchemyError as exc:
        log.error("assets.list.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    # One lookup for the page, so a UUID becomes a word without a second service.
    names = await asset_catalogue.names_for(session, [r.category_id for r in rows])
    out = []
    for row in rows:
        item = AssetResponse.model_validate(row)
        item.category_name = names.get(str(row.category_id)) if row.category_id else None
        out.append(item)
    response.headers["X-Total-Count"] = str(total)
    log.debug("assets.list.result", count=len(out), total=total, q=q)
    return out


@router.get(
    "/asset-categories",
    response_model=List[AssetCategoryResponse],
    summary="List asset categories",
    description=(
        "Resolves the category_id foreign key on assets to a name. Categories are "
        "company-wide: the table carries no building column."
    ),
    tags=["Assets"],
)
async def list_asset_categories(
    limit:     int          = Query(500, ge=1, le=2000),
    session:   AsyncSession = Depends(get_session),
    principal: Principal    = Depends(current_principal),
):
    try:
        rows = await asset_catalogue.list_categories(
            session, organization_id=principal.organization_id, limit=limit
        )
    except SQLAlchemyError as exc:
        log.error("asset_categories.list.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    return [AssetCategoryResponse(**r) for r in rows]


@router.get(
    "/assets/{asset_id}",
    response_model=AssetResponse,
    summary="Get an asset by ID",
    responses={404: {"description": "Asset not found"}},
    tags=["Assets"],
)
async def get_asset(asset_id: str, session: AsyncSession = Depends(get_session),
                    principal: Principal = Depends(current_principal)):
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
    assert_building(principal, asset.building_id)
    item = AssetResponse.model_validate(asset)
    if asset.category_id:
        names = await asset_catalogue.names_for(session, [asset.category_id])
        item.category_name = names.get(str(asset.category_id))
    log.debug("assets.get.found", asset_id=asset_id, asset_name=asset.asset_name)
    return item


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
    principal: Principal = Depends(current_principal),
):
    # Real plenum_cafm.locations has no 'active' column — return all
    query = scope_select(select(Location), principal, Location.building_id)
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
