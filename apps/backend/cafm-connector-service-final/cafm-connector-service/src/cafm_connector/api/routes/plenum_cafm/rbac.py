"""CRUD routes — Roles, Permissions, User-Roles, Role-Permissions."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    PermissionCreate, PermissionResponse, PermissionUpdate,
    RoleCreate, RolePermissionCreate, RolePermissionResponse,
    RoleResponse, RoleUpdate,
    UserRoleCreate, UserRoleResponse,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import Permission, Role, RolePermission, UserRole

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# ROLES
# ══════════════════════════════════════════════════════════════════════

@router.get("/roles", response_model=PaginatedResponse, tags=["Roles & Permissions"])
async def list_roles(
    organization_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Role)
    if organization_id:
        stmt = stmt.where(Role.organization_id == organization_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Role.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[RoleResponse.model_validate(r) for r in rows])


@router.get("/roles/{role_id}", response_model=RoleResponse, tags=["Roles & Permissions"])
async def get_role(role_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Role, role_id)
    if not row:
        raise HTTPException(404, "Role not found")
    return RoleResponse.model_validate(row)


@router.post("/roles", response_model=RoleResponse, status_code=201, tags=["Roles & Permissions"])
async def create_role(body: RoleCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Role(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return RoleResponse.model_validate(obj)


@router.put("/roles/{role_id}", response_model=RoleResponse, tags=["Roles & Permissions"])
async def update_role(role_id: str, body: RoleUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Role, role_id)
    if not obj:
        raise HTTPException(404, "Role not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return RoleResponse.model_validate(obj)


@router.delete("/roles/{role_id}", status_code=200, tags=["Roles & Permissions"])
async def delete_role(role_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Role, role_id)
    if not obj:
        raise HTTPException(404, "Role not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# PERMISSIONS
# ══════════════════════════════════════════════════════════════════════

@router.get("/permissions", response_model=PaginatedResponse, tags=["Roles & Permissions"])
async def list_permissions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    total = (await db.execute(select(func.count()).select_from(Permission))).scalar_one()
    rows = (await db.execute(select(Permission).order_by(Permission.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[PermissionResponse.model_validate(r) for r in rows])


@router.get("/permissions/{perm_id}", response_model=PermissionResponse, tags=["Roles & Permissions"])
async def get_permission(perm_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Permission, perm_id)
    if not row:
        raise HTTPException(404, "Permission not found")
    return PermissionResponse.model_validate(row)


@router.post("/permissions", response_model=PermissionResponse, status_code=201, tags=["Roles & Permissions"])
async def create_permission(body: PermissionCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Permission(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return PermissionResponse.model_validate(obj)


@router.put("/permissions/{perm_id}", response_model=PermissionResponse, tags=["Roles & Permissions"])
async def update_permission(perm_id: str, body: PermissionUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Permission, perm_id)
    if not obj:
        raise HTTPException(404, "Permission not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return PermissionResponse.model_validate(obj)


@router.delete("/permissions/{perm_id}", status_code=200, tags=["Roles & Permissions"])
async def delete_permission(perm_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Permission, perm_id)
    if not obj:
        raise HTTPException(404, "Permission not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# USER ROLES  (junction)
# ══════════════════════════════════════════════════════════════════════

@router.get("/user-roles", response_model=PaginatedResponse, tags=["Roles & Permissions"])
async def list_user_roles(
    user_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(UserRole)
    if user_id:
        stmt = stmt.where(UserRole.user_id == user_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(UserRole.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[UserRoleResponse.model_validate(r) for r in rows])


@router.post("/user-roles", response_model=UserRoleResponse, status_code=201, tags=["Roles & Permissions"])
async def create_user_role(body: UserRoleCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = UserRole(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return UserRoleResponse.model_validate(obj)


@router.delete("/user-roles/{user_role_id}", status_code=200, tags=["Roles & Permissions"])
async def delete_user_role(user_role_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(UserRole, user_role_id)
    if not obj:
        raise HTTPException(404, "UserRole not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# ROLE PERMISSIONS  (junction)
# ══════════════════════════════════════════════════════════════════════

@router.get("/role-permissions", response_model=PaginatedResponse, tags=["Roles & Permissions"])
async def list_role_permissions(
    role_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(RolePermission)
    if role_id:
        stmt = stmt.where(RolePermission.role_id == role_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(RolePermission.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[RolePermissionResponse.model_validate(r) for r in rows])


@router.post("/role-permissions", response_model=RolePermissionResponse, status_code=201, tags=["Roles & Permissions"])
async def create_role_permission(body: RolePermissionCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = RolePermission(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return RolePermissionResponse.model_validate(obj)


@router.delete("/role-permissions/{rp_id}", status_code=200, tags=["Roles & Permissions"])
async def delete_role_permission(rp_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(RolePermission, rp_id)
    if not obj:
        raise HTTPException(404, "RolePermission not found")
    await db.delete(obj)
    await db.commit()