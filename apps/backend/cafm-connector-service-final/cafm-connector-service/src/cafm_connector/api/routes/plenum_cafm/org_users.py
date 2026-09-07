"""CRUD routes — Organizations & Users."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    OrganizationCreate, OrganizationResponse, OrganizationUpdate,
    UserCreate, UserResponse, UserUpdate,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import Organization, User

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# ORGANIZATIONS
# ══════════════════════════════════════════════════════════════════════

@router.get("/organizations", response_model=PaginatedResponse, tags=["Organizations"])
async def list_organizations(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    total_q = await db.execute(select(func.count()).select_from(Organization))
    total = total_q.scalar_one()
    rows_q = await db.execute(select(Organization).order_by(Organization.created_at.desc()).offset(offset).limit(limit))
    rows = rows_q.scalars().all()
    return PaginatedResponse(
        total=total, limit=limit, offset=offset,
        data=[OrganizationResponse.model_validate(r) for r in rows],
    )


@router.get("/organizations/{org_id}", response_model=OrganizationResponse, tags=["Organizations"])
async def get_organization(org_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Organization, org_id)
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return OrganizationResponse.model_validate(row)


@router.post("/organizations", response_model=OrganizationResponse, status_code=201, tags=["Organizations"])
async def create_organization(body: OrganizationCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Organization(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return OrganizationResponse.model_validate(obj)


@router.put("/organizations/{org_id}", response_model=OrganizationResponse, tags=["Organizations"])
async def update_organization(
    org_id: str, body: OrganizationUpdate, db: AsyncSession = Depends(get_plenum_db)
):
    obj = await db.get(Organization, org_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Organization not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return OrganizationResponse.model_validate(obj)


@router.delete("/organizations/{org_id}", status_code=200, tags=["Organizations"])
async def delete_organization(org_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Organization, org_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Organization not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# USERS
# ══════════════════════════════════════════════════════════════════════

@router.get("/users", response_model=PaginatedResponse, tags=["Users"])
async def list_users(
    organization_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(User)
    if organization_id:
        stmt = stmt.where(User.organization_id == organization_id)
    total_q = await db.execute(select(func.count()).select_from(stmt.subquery()))
    total = total_q.scalar_one()
    rows_q = await db.execute(stmt.order_by(User.created_at.desc()).offset(offset).limit(limit))
    rows = rows_q.scalars().all()
    return PaginatedResponse(
        total=total, limit=limit, offset=offset,
        data=[UserResponse.model_validate(r) for r in rows],
    )


@router.get("/users/{user_id}", response_model=UserResponse, tags=["Users"])
async def get_user(user_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(row)


@router.post("/users", response_model=UserResponse, status_code=201, tags=["Users"])
async def create_user(body: UserCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = User(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return UserResponse.model_validate(obj)


@router.put("/users/{user_id}", response_model=UserResponse, tags=["Users"])
async def update_user(user_id: str, body: UserUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(User, user_id)
    if not obj:
        raise HTTPException(status_code=404, detail="User not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)
    await db.commit()
    await db.refresh(obj)
    return UserResponse.model_validate(obj)


@router.delete("/users/{user_id}", status_code=200, tags=["Users"])
async def delete_user(user_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(User, user_id)
    if not obj:
        raise HTTPException(status_code=404, detail="User not found")
    await db.delete(obj)
    await db.commit()