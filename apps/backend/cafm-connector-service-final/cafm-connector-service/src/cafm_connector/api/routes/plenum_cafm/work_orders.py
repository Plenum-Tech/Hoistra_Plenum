"""CRUD routes — Work Orders, Tasks, Comments, Attachments, History."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.api.routes.plenum_cafm.deps import get_plenum_db
from cafm_connector.api.schemas.plenum_cafm import (
    MaintenanceHistoryCreate, MaintenanceHistoryResponse, MaintenanceHistoryUpdate,
    WorkOrderAttachmentCreate, WorkOrderAttachmentResponse,
    WorkOrderCommentCreate, WorkOrderCommentResponse, WorkOrderCommentUpdate,
    WorkOrderCreate, WorkOrderHistoryCreate, WorkOrderHistoryResponse,
    WorkOrderPartCreate, WorkOrderPartResponse, WorkOrderPartUpdate,
    WorkOrderResponse, WorkOrderTaskCreate, WorkOrderTaskResponse,
    WorkOrderTaskUpdate, WorkOrderUpdate,
    PaginatedResponse,
)
from cafm_connector.models.plenum_cafm import (
    MaintenanceHistory, WorkOrder, WorkOrderAttachment,
    WorkOrderComment, WorkOrderHistory, WorkOrderPart, WorkOrderTask,
)

router = APIRouter()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDERS
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-orders", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_orders(
    organization_id: str | None = Query(None),
    asset_id: str | None = Query(None),
    status: str | None = Query(None),
    priority: str | None = Query(None),
    assigned_technician: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrder)
    if organization_id:
        stmt = stmt.where(WorkOrder.organization_id == organization_id)
    if asset_id:
        stmt = stmt.where(WorkOrder.asset_id == asset_id)
    if status:
        stmt = stmt.where(WorkOrder.status == status)
    if priority:
        stmt = stmt.where(WorkOrder.priority == priority)
    if assigned_technician:
        stmt = stmt.where(WorkOrder.assigned_technician == assigned_technician)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrder.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderResponse.model_validate(r) for r in rows])


@router.get("/work-orders/{wo_id}", response_model=WorkOrderResponse, tags=["Work Orders"])
async def get_work_order(wo_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrder, wo_id)
    if not row:
        raise HTTPException(404, "Work order not found")
    return WorkOrderResponse.model_validate(row)


@router.post("/work-orders", response_model=WorkOrderResponse, status_code=201, tags=["Work Orders"])
async def create_work_order(body: WorkOrderCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrder(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderResponse.model_validate(obj)


@router.put("/work-orders/{wo_id}", response_model=WorkOrderResponse, tags=["Work Orders"])
async def update_work_order(wo_id: str, body: WorkOrderUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrder, wo_id)
    if not obj:
        raise HTTPException(404, "Work order not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderResponse.model_validate(obj)


@router.delete("/work-orders/{wo_id}", status_code=200, tags=["Work Orders"])
async def delete_work_order(wo_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrder, wo_id)
    if not obj:
        raise HTTPException(404, "Work order not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER TASKS
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-order-tasks", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_order_tasks(
    work_order_id: str | None = Query(None),
    assigned_to: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrderTask)
    if work_order_id:
        stmt = stmt.where(WorkOrderTask.work_order_id == work_order_id)
    if assigned_to:
        stmt = stmt.where(WorkOrderTask.assigned_to == assigned_to)
    if status:
        stmt = stmt.where(WorkOrderTask.status == status)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrderTask.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderTaskResponse.model_validate(r) for r in rows])


@router.get("/work-order-tasks/{task_id}", response_model=WorkOrderTaskResponse, tags=["Work Orders"])
async def get_work_order_task(task_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrderTask, task_id)
    if not row:
        raise HTTPException(404, "Work order task not found")
    return WorkOrderTaskResponse.model_validate(row)


@router.post("/work-order-tasks", response_model=WorkOrderTaskResponse, status_code=201, tags=["Work Orders"])
async def create_work_order_task(body: WorkOrderTaskCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrderTask(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderTaskResponse.model_validate(obj)


@router.put("/work-order-tasks/{task_id}", response_model=WorkOrderTaskResponse, tags=["Work Orders"])
async def update_work_order_task(task_id: str, body: WorkOrderTaskUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderTask, task_id)
    if not obj:
        raise HTTPException(404, "Work order task not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderTaskResponse.model_validate(obj)


@router.delete("/work-order-tasks/{task_id}", status_code=200, tags=["Work Orders"])
async def delete_work_order_task(task_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderTask, task_id)
    if not obj:
        raise HTTPException(404, "Work order task not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER COMMENTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-order-comments", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_order_comments(
    work_order_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrderComment)
    if work_order_id:
        stmt = stmt.where(WorkOrderComment.work_order_id == work_order_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrderComment.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderCommentResponse.model_validate(r) for r in rows])


@router.get("/work-order-comments/{comment_id}", response_model=WorkOrderCommentResponse, tags=["Work Orders"])
async def get_work_order_comment(comment_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrderComment, comment_id)
    if not row:
        raise HTTPException(404, "Comment not found")
    return WorkOrderCommentResponse.model_validate(row)


@router.post("/work-order-comments", response_model=WorkOrderCommentResponse, status_code=201, tags=["Work Orders"])
async def create_work_order_comment(body: WorkOrderCommentCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrderComment(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderCommentResponse.model_validate(obj)


@router.put("/work-order-comments/{comment_id}", response_model=WorkOrderCommentResponse, tags=["Work Orders"])
async def update_work_order_comment(comment_id: str, body: WorkOrderCommentUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderComment, comment_id)
    if not obj:
        raise HTTPException(404, "Comment not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderCommentResponse.model_validate(obj)


@router.delete("/work-order-comments/{comment_id}", status_code=200, tags=["Work Orders"])
async def delete_work_order_comment(comment_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderComment, comment_id)
    if not obj:
        raise HTTPException(404, "Comment not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER ATTACHMENTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-order-attachments", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_order_attachments(
    work_order_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrderAttachment)
    if work_order_id:
        stmt = stmt.where(WorkOrderAttachment.work_order_id == work_order_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrderAttachment.uploaded_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderAttachmentResponse.model_validate(r) for r in rows])


@router.get("/work-order-attachments/{att_id}", response_model=WorkOrderAttachmentResponse, tags=["Work Orders"])
async def get_work_order_attachment(att_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrderAttachment, att_id)
    if not row:
        raise HTTPException(404, "Attachment not found")
    return WorkOrderAttachmentResponse.model_validate(row)


@router.post("/work-order-attachments", response_model=WorkOrderAttachmentResponse, status_code=201, tags=["Work Orders"])
async def create_work_order_attachment(body: WorkOrderAttachmentCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrderAttachment(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderAttachmentResponse.model_validate(obj)


@router.delete("/work-order-attachments/{att_id}", status_code=200, tags=["Work Orders"])
async def delete_work_order_attachment(att_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderAttachment, att_id)
    if not obj:
        raise HTTPException(404, "Attachment not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER HISTORY  (append-only — no PUT/DELETE)
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-order-history", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_order_history(
    work_order_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrderHistory)
    if work_order_id:
        stmt = stmt.where(WorkOrderHistory.work_order_id == work_order_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrderHistory.changed_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderHistoryResponse.model_validate(r) for r in rows])


@router.get("/work-order-history/{hist_id}", response_model=WorkOrderHistoryResponse, tags=["Work Orders"])
async def get_work_order_history_entry(hist_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrderHistory, hist_id)
    if not row:
        raise HTTPException(404, "History entry not found")
    return WorkOrderHistoryResponse.model_validate(row)


@router.post("/work-order-history", response_model=WorkOrderHistoryResponse, status_code=201, tags=["Work Orders"])
async def create_work_order_history(body: WorkOrderHistoryCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrderHistory(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderHistoryResponse.model_validate(obj)


# ══════════════════════════════════════════════════════════════════════
# MAINTENANCE HISTORY
# ══════════════════════════════════════════════════════════════════════

@router.get("/maintenance-history", response_model=PaginatedResponse, tags=["Maintenance"])
async def list_maintenance_history(
    asset_id: str | None = Query(None),
    work_order_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(MaintenanceHistory)
    if asset_id:
        stmt = stmt.where(MaintenanceHistory.asset_id == asset_id)
    if work_order_id:
        stmt = stmt.where(MaintenanceHistory.work_order_id == work_order_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(MaintenanceHistory.performed_at.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[MaintenanceHistoryResponse.model_validate(r) for r in rows])


@router.get("/maintenance-history/{mh_id}", response_model=MaintenanceHistoryResponse, tags=["Maintenance"])
async def get_maintenance_history(mh_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(MaintenanceHistory, mh_id)
    if not row:
        raise HTTPException(404, "Maintenance history entry not found")
    return MaintenanceHistoryResponse.model_validate(row)


@router.post("/maintenance-history", response_model=MaintenanceHistoryResponse, status_code=201, tags=["Maintenance"])
async def create_maintenance_history(body: MaintenanceHistoryCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = MaintenanceHistory(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return MaintenanceHistoryResponse.model_validate(obj)


@router.put("/maintenance-history/{mh_id}", response_model=MaintenanceHistoryResponse, tags=["Maintenance"])
async def update_maintenance_history(mh_id: str, body: MaintenanceHistoryUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(MaintenanceHistory, mh_id)
    if not obj:
        raise HTTPException(404, "Maintenance history entry not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return MaintenanceHistoryResponse.model_validate(obj)


@router.delete("/maintenance-history/{mh_id}", status_code=200, tags=["Maintenance"])
async def delete_maintenance_history(mh_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(MaintenanceHistory, mh_id)
    if not obj:
        raise HTTPException(404, "Maintenance history entry not found")
    await db.delete(obj)
    await db.commit()


# ══════════════════════════════════════════════════════════════════════
# WORK ORDER PARTS
# ══════════════════════════════════════════════════════════════════════

@router.get("/work-order-parts", response_model=PaginatedResponse, tags=["Work Orders"])
async def list_work_order_parts(
    work_order_id: str | None = Query(None),
    part_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_plenum_db),
):
    stmt = select(WorkOrderPart)
    if work_order_id:
        stmt = stmt.where(WorkOrderPart.work_order_id == work_order_id)
    if part_id:
        stmt = stmt.where(WorkOrderPart.part_id == part_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(WorkOrderPart.id.desc()).offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(total=total, limit=limit, offset=offset,
                             data=[WorkOrderPartResponse.model_validate(r) for r in rows])


@router.get("/work-order-parts/{wp_id}", response_model=WorkOrderPartResponse, tags=["Work Orders"])
async def get_work_order_part(wp_id: str, db: AsyncSession = Depends(get_plenum_db)):
    row = await db.get(WorkOrderPart, wp_id)
    if not row:
        raise HTTPException(404, "Work order part not found")
    return WorkOrderPartResponse.model_validate(row)


@router.post("/work-order-parts", response_model=WorkOrderPartResponse, status_code=201, tags=["Work Orders"])
async def create_work_order_part(body: WorkOrderPartCreate, db: AsyncSession = Depends(get_plenum_db)):
    obj = WorkOrderPart(id=uuid4(), **body.model_dump())
    db.add(obj)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderPartResponse.model_validate(obj)


@router.put("/work-order-parts/{wp_id}", response_model=WorkOrderPartResponse, tags=["Work Orders"])
async def update_work_order_part(wp_id: str, body: WorkOrderPartUpdate, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderPart, wp_id)
    if not obj:
        raise HTTPException(404, "Work order part not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)
    await db.commit()
    await db.refresh(obj)
    return WorkOrderPartResponse.model_validate(obj)


@router.delete("/work-order-parts/{wp_id}", status_code=200, tags=["Work Orders"])
async def delete_work_order_part(wp_id: str, db: AsyncSession = Depends(get_plenum_db)):
    obj = await db.get(WorkOrderPart, wp_id)
    if not obj:
        raise HTTPException(404, "Work order part not found")
    await db.delete(obj)
    await db.commit()