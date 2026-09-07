"""CRUD routes — Locations, Asset Categories, Assets, Asset Documents, Asset Readings."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    AssetCategoryCreate, AssetCategoryResponse, AssetCategoryUpdate,
    AssetCreate, AssetDocumentCreate, AssetDocumentResponse, AssetDocumentUpdate,
    AssetReadingCreate, AssetReadingResponse, AssetReadingUpdate,
    AssetResponse, AssetUpdate,
    LocationCreate, LocationResponse, LocationUpdate,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import Asset, AssetCategory, AssetDocument, AssetReading, Location

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# LOCATIONS
# ══════════════════════════════════════════════════════════════════════

@router.get("/locations", response_model=PaginatedResponse, tags=["Locations"])
async def list_locations(
    organization_id: str | None = Query(None),
    parent_location_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Location)
    if organization_id:
        stmt = stmt.where(Location.organization_id == organization_id)
    if parent_location_id:
        stmt = stmt.where(Location.parent_location_id == parent_location_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Location.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[LocationResponse.model_validate(r) for r in rows])


@router.get("/locations/{location_id}", response_model=LocationResponse, tags=["Locations"])
async def get_location(location_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Location, location_id)
    if not row:
        raise HTTPException(404, "Location not found")
    return LocationResponse.model_validate(row)


@router.post("/locations", response_model=LocationResponse, status_code=201, tags=["Locations"])
async def create_location(body: LocationCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Location(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return LocationResponse.model_validate(obj)


@router.put("/locations/{location_id}", response_model=LocationResponse, tags=["Locations"])
async def update_location(location_id: str, body: LocationUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Location, location_id)
    if not obj:
        raise HTTPException(404, "Location not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return LocationResponse.model_validate(obj)


@router.delete("/locations/{location_id}", status_code=200, tags=["Locations"])
async def delete_location(location_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Location, location_id)
    if not obj:
        raise HTTPException(404, "Location not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# ASSET CATEGORIES
# ══════════════════════════════════════════════════════════════════════

@router.get("/asset-categories", response_model=PaginatedResponse, tags=["Assets"])
async def list_asset_categories(
    organization_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(AssetCategory)
    if organization_id:
        stmt = stmt.where(AssetCategory.organization_id == organization_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(AssetCategory.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[AssetCategoryResponse.model_validate(r) for r in rows])


@router.get("/asset-categories/{cat_id}", response_model=AssetCategoryResponse, tags=["Assets"])
async def get_asset_category(cat_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(AssetCategory, cat_id)
    if not row:
        raise HTTPException(404, "Asset category not found")
    return AssetCategoryResponse.model_validate(row)


@router.post("/asset-categories", response_model=AssetCategoryResponse, status_code=201, tags=["Assets"])
async def create_asset_category(body: AssetCategoryCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = AssetCategory(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return AssetCategoryResponse.model_validate(obj)


@router.put("/asset-categories/{cat_id}", response_model=AssetCategoryResponse, tags=["Assets"])
async def update_asset_category(cat_id: str, body: AssetCategoryUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetCategory, cat_id)
    if not obj:
        raise HTTPException(404, "Asset category not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return AssetCategoryResponse.model_validate(obj)


@router.delete("/asset-categories/{cat_id}", status_code=200, tags=["Assets"])
async def delete_asset_category(cat_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetCategory, cat_id)
    if not obj:
        raise HTTPException(404, "Asset category not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# ASSETS
# ══════════════════════════════════════════════════════════════════════

@router.get("/assets", response_model=PaginatedResponse, tags=["Assets"])
async def list_assets(
    organization_id: str | None = Query(None),
    location_id: str | None = Query(None),
    category_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Asset)
    if organization_id:
        stmt = stmt.where(Asset.organization_id == organization_id)
    if location_id:
        stmt = stmt.where(Asset.location_id == location_id)
    if category_id:
        stmt = stmt.where(Asset.category_id == category_id)
    if status:
        stmt = stmt.where(Asset.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Asset.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[AssetResponse.model_validate(r) for r in rows])


@router.get("/assets/{asset_id}", response_model=AssetResponse, tags=["Assets"])
async def get_asset(asset_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Asset, asset_id)
    if not row:
        raise HTTPException(404, "Asset not found")
    return AssetResponse.model_validate(row)


@router.post("/assets", response_model=AssetResponse, status_code=201, tags=["Assets"])
async def create_asset(body: AssetCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Asset(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return AssetResponse.model_validate(obj)


@router.put("/assets/{asset_id}", response_model=AssetResponse, tags=["Assets"])
async def update_asset(asset_id: str, body: AssetUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Asset, asset_id)
    if not obj:
        raise HTTPException(404, "Asset not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return AssetResponse.model_validate(obj)


@router.delete("/assets/{asset_id}", status_code=200, tags=["Assets"])
async def delete_asset(asset_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Asset, asset_id)
    if not obj:
        raise HTTPException(404, "Asset not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# ASSET DOCUMENTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/asset-documents", response_model=PaginatedResponse, tags=["Assets"])
async def list_asset_documents(
    asset_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(AssetDocument)
    if asset_id:
        stmt = stmt.where(AssetDocument.asset_id == asset_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(AssetDocument.uploaded_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[AssetDocumentResponse.model_validate(r) for r in rows])


@router.get("/asset-documents/{doc_id}", response_model=AssetDocumentResponse, tags=["Assets"])
async def get_asset_document(doc_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(AssetDocument, doc_id)
    if not row:
        raise HTTPException(404, "Asset document not found")
    return AssetDocumentResponse.model_validate(row)


@router.post("/asset-documents", response_model=AssetDocumentResponse, status_code=201, tags=["Assets"])
async def create_asset_document(body: AssetDocumentCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = AssetDocument(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return AssetDocumentResponse.model_validate(obj)


@router.put("/asset-documents/{doc_id}", response_model=AssetDocumentResponse, tags=["Assets"])
async def update_asset_document(doc_id: str, body: AssetDocumentUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetDocument, doc_id)
    if not obj:
        raise HTTPException(404, "Asset document not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return AssetDocumentResponse.model_validate(obj)


@router.delete("/asset-documents/{doc_id}", status_code=200, tags=["Assets"])
async def delete_asset_document(doc_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetDocument, doc_id)
    if not obj:
        raise HTTPException(404, "Asset document not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# ASSET READINGS
# ══════════════════════════════════════════════════════════════════════

@router.get("/asset-readings", response_model=PaginatedResponse, tags=["Assets"])
async def list_asset_readings(
    asset_id: str | None = Query(None),
    organization_id: str | None = Query(None),
    reading_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(AssetReading)
    if asset_id:
        stmt = stmt.where(AssetReading.asset_id == asset_id)
    if organization_id:
        stmt = stmt.where(AssetReading.organization_id == organization_id)
    if reading_type:
        stmt = stmt.where(AssetReading.reading_type == reading_type)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(AssetReading.recorded_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[AssetReadingResponse.model_validate(r) for r in rows])


@router.get("/asset-readings/{reading_id}", response_model=AssetReadingResponse, tags=["Assets"])
async def get_asset_reading(reading_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(AssetReading, reading_id)
    if not row:
        raise HTTPException(404, "Asset reading not found")
    return AssetReadingResponse.model_validate(row)


@router.post("/asset-readings", response_model=AssetReadingResponse, status_code=201, tags=["Assets"])
async def create_asset_reading(body: AssetReadingCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = AssetReading(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return AssetReadingResponse.model_validate(obj)


@router.put("/asset-readings/{reading_id}", response_model=AssetReadingResponse, tags=["Assets"])
async def update_asset_reading(reading_id: str, body: AssetReadingUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetReading, reading_id)
    if not obj:
        raise HTTPException(404, "Asset reading not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return AssetReadingResponse.model_validate(obj)


@router.delete("/asset-readings/{reading_id}", status_code=200, tags=["Assets"])
async def delete_asset_reading(reading_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(AssetReading, reading_id)
    if not obj:
        raise HTTPException(404, "Asset reading not found")
    await db.delete(obj)
    await db.commit()