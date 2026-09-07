"""CRUD routes — Spare Parts, Inventory Transactions, Notifications, Audit Logs."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    AuditLogCreate, AuditLogResponse,
    InventoryTransactionCreate, InventoryTransactionResponse,
    NotificationCreate, NotificationResponse, NotificationUpdate,
    SparePartCreate, SparePartResponse, SparePartUpdate,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import AuditLog, InventoryTransaction, Notification, SparePart

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# SPARE PARTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/spare-parts", response_model=PaginatedResponse, tags=["Inventory"])
async def list_spare_parts(
    organization_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(SparePart)
    if organization_id:
        stmt = stmt.where(SparePart.organization_id == organization_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(SparePart.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[SparePartResponse.model_validate(r) for r in rows])


@router.get("/spare-parts/{part_id}", response_model=SparePartResponse, tags=["Inventory"])
async def get_spare_part(part_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(SparePart, part_id)
    if not row:
        raise HTTPException(404, "Spare part not found")
    return SparePartResponse.model_validate(row)


@router.post("/spare-parts", response_model=SparePartResponse, status_code=201, tags=["Inventory"])
async def create_spare_part(body: SparePartCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = SparePart(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return SparePartResponse.model_validate(obj)


@router.put("/spare-parts/{part_id}", response_model=SparePartResponse, tags=["Inventory"])
async def update_spare_part(part_id: str, body: SparePartUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(SparePart, part_id)
    if not obj:
        raise HTTPException(404, "Spare part not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return SparePartResponse.model_validate(obj)


@router.delete("/spare-parts/{part_id}", status_code=200, tags=["Inventory"])
async def delete_spare_part(part_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(SparePart, part_id)
    if not obj:
        raise HTTPException(404, "Spare part not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# INVENTORY TRANSACTIONS  (append-only — no PUT/DELETE in practice)
# ══════════════════════════════════════════════════════════════════════

@router.get("/inventory-transactions", response_model=PaginatedResponse, tags=["Inventory"])
async def list_inventory_transactions(
    part_id: str | None = Query(None),
    transaction_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(InventoryTransaction)
    if part_id:
        stmt = stmt.where(InventoryTransaction.part_id == part_id)
    if transaction_type:
        stmt = stmt.where(InventoryTransaction.transaction_type == transaction_type)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(InventoryTransaction.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[InventoryTransactionResponse.model_validate(r) for r in rows])


@router.get("/inventory-transactions/{txn_id}", response_model=InventoryTransactionResponse, tags=["Inventory"])
async def get_inventory_transaction(txn_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(InventoryTransaction, txn_id)
    if not row:
        raise HTTPException(404, "Inventory transaction not found")
    return InventoryTransactionResponse.model_validate(row)


@router.post("/inventory-transactions", response_model=InventoryTransactionResponse, status_code=201, tags=["Inventory"])
async def create_inventory_transaction(body: InventoryTransactionCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = InventoryTransaction(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return InventoryTransactionResponse.model_validate(obj)


@router.delete("/inventory-transactions/{txn_id}", status_code=200, tags=["Inventory"])
async def delete_inventory_transaction(txn_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(InventoryTransaction, txn_id)
    if not obj:
        raise HTTPException(404, "Inventory transaction not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════

@router.get("/notifications", response_model=PaginatedResponse, tags=["Notifications"])
async def list_notifications(
    organization_id: str | None = Query(None),
    user_id: str | None = Query(None),
    is_read: bool | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(Notification)
    if organization_id:
        stmt = stmt.where(Notification.organization_id == organization_id)
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    if is_read is not None:
        stmt = stmt.where(Notification.is_read == is_read)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Notification.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[NotificationResponse.model_validate(r) for r in rows])


@router.get("/notifications/{notif_id}", response_model=NotificationResponse, tags=["Notifications"])
async def get_notification(notif_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(Notification, notif_id)
    if not row:
        raise HTTPException(404, "Notification not found")
    return NotificationResponse.model_validate(row)


@router.post("/notifications", response_model=NotificationResponse, status_code=201, tags=["Notifications"])
async def create_notification(body: NotificationCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = Notification(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return NotificationResponse.model_validate(obj)


@router.put("/notifications/{notif_id}", response_model=NotificationResponse, tags=["Notifications"])
async def update_notification(notif_id: str, body: NotificationUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Notification, notif_id)
    if not obj:
        raise HTTPException(404, "Notification not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return NotificationResponse.model_validate(obj)


@router.delete("/notifications/{notif_id}", status_code=200, tags=["Notifications"])
async def delete_notification(notif_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(Notification, notif_id)
    if not obj:
        raise HTTPException(404, "Notification not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# AUDIT LOGS  (append-only — no PUT/DELETE)
# ══════════════════════════════════════════════════════════════════════

@router.get("/audit-logs", response_model=PaginatedResponse, tags=["Audit"])
async def list_audit_logs(
    organization_id: str | None = Query(None),
    user_id: str | None = Query(None),
    entity_type: str | None = Query(None),
    entity_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(AuditLog)
    if organization_id:
        stmt = stmt.where(AuditLog.organization_id == organization_id)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[AuditLogResponse.model_validate(r) for r in rows])


@router.get("/audit-logs/{log_id}", response_model=AuditLogResponse, tags=["Audit"])
async def get_audit_log(log_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(AuditLog, log_id)
    if not row:
        raise HTTPException(404, "Audit log entry not found")
    return AuditLogResponse.model_validate(row)


@router.post("/audit-logs", response_model=AuditLogResponse, status_code=201, tags=["Audit"])
async def create_audit_log(body: AuditLogCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = AuditLog(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return AuditLogResponse.model_validate(obj)