"""BE1-05/08/09/13/14 + BE2-09/12/16 — Work Order CRUD + status machine + history + bulk."""
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field


from ...config import settings
from ...core.logging import get_logger
from ...db import get_session
from ...models.work_order import WorkOrder
from ...models.status_history import StatusHistory
from ...api.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderCreateResponse,
    WorkOrderUpdate,
    WorkOrderResponse,
    StatusUpdate,
)
from ...services.approval_chain_service import approval_suggestion_after_create
from ...api.schemas.journey import StatusHistoryEntry, BulkStatusUpdate
from ...core.exceptions import (
    WorkOrderNotFound, InvalidStatusTransition,
    WorkOrderAlreadyClosed, ApprovalNotPending, DatabaseError,
)
from ...services.journey_service import (
    create_journey_for_work_order,
    record_status_change,
    advance_journey_milestone,
)
from ...services.approval_workflow import ApprovalWorkflowService

router = APIRouter()
log = get_logger(__name__)

_VALID_TRANSITIONS: dict[str, list[str]] = {
    "pending_approval": ["preparing", "closed"],
    "preparing":        ["prepared",  "closed"],
    "prepared":         ["active",    "preparing", "closed"],
    "active":           ["completed", "closed"],
    "completed":        ["closed"],
    "closed":           [],
}

_404 = {404: {"description": "Work order not found"}}
_422 = {422: {"description": "Validation error"}}
_404_422 = {**_404, **_422}


def _wo_id() -> str:
    return f"WO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"


async def _get_wo_or_404(work_order_id: str, session: AsyncSession) -> WorkOrder:
    try:
        result = await session.execute(
            select(WorkOrder).where(WorkOrder.work_order_id == work_order_id)
        )
    except SQLAlchemyError as exc:
        raise DatabaseError(str(exc)) from exc
    wo = result.scalar_one_or_none()
    if not wo:
        raise WorkOrderNotFound(work_order_id)
    return wo


# ── Create (BE2-06: also auto-creates journey) ────────────────────────────────

@router.post(
    "/",
    response_model=WorkOrderCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a work order",
    responses=_422,
)
async def create_work_order(
    payload: WorkOrderCreate,
    session: AsyncSession = Depends(get_session),
):
    log.info(
        "work_order.create.start",
        source=payload.source,
        asset=payload.asset,
        priority=payload.priority,
        requester_email=payload.requester_email,
    )
    org_id = None
    if settings.default_organization_id:
        try:
            org_id = int(settings.default_organization_id)
        except (TypeError, ValueError):
            log.warning("work_order.create.bad_org_id", value=settings.default_organization_id)

    wo = WorkOrder(
        work_order_id=_wo_id(),
        organization_id=org_id,
        title=payload.issue_description,
        source=payload.source,
        asset=payload.asset,
        location=payload.location,
        issue_description=payload.issue_description,
        priority=payload.priority,
        request_type=payload.request_type,
        status="pending_approval",
        approval_type="preparation",
        requester_name=payload.requester_name,
        requester_email=str(payload.requester_email),
        requester_phone=payload.requester_phone,
    )
    try:
        session.add(wo)
        await session.flush()

        jlog = await create_journey_for_work_order(
            wo.work_order_id, payload.priority, session
        )
        wo.journey_log_id = jlog.jlog_id

        await record_status_change(
            wo.work_order_id, None, "pending_approval", session
        )

        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.create.db_error", exc_info=exc)
        raise DatabaseError(f"Failed to create work order: {exc}") from exc

    log.info(
        "work_order.created",
        work_order_id=wo.work_order_id,
        journey_log_id=wo.journey_log_id,
        priority=wo.priority,
        source=wo.source,
    )

    approval_preview: dict = {}
    try:
        approval_preview = await approval_suggestion_after_create(session, wo)
    except Exception as exc:
        log.warning(
            "work_order.create.approval_suggestion_failed",
            work_order_id=wo.work_order_id,
            error=str(exc),
            exc_info=True,
        )

    auto = approval_preview.get("auto_suggestion") or {}
    base_message = (
        f"Work order **{wo.work_order_id}** created successfully. "
        f"Status: pending_approval | Priority: {wo.priority}"
    )
    if auto.get("message"):
        base_message += f"\n\n{auto['message']}"

    base = WorkOrderResponse.model_validate(wo)
    return WorkOrderCreateResponse(
        **base.model_dump(),
        approval_suggestion=approval_preview or None,
        auto_suggestion=auto or None,
        message=base_message,
    )


# ── List (BE2-16: pagination added) ──────────────────────────────────────────

@router.get(
    "/",
    response_model=List[WorkOrderResponse],
    summary="List work orders",
)
async def list_work_orders(
    status_filter: Optional[str]      = Query(None, alias="status"),
    priority:      Optional[str]      = Query(None),
    asset:         Optional[str]      = Query(None),
    from_date:     Optional[datetime] = Query(None),
    to_date:       Optional[datetime] = Query(None),
    page:          int                = Query(1, ge=1),
    limit:         int                = Query(20, ge=1, le=200),
    session:       AsyncSession       = Depends(get_session),
):
    log.debug(
        "work_order.list",
        status_filter=status_filter, priority=priority,
        asset=asset, page=page, limit=limit,
    )
    q = select(WorkOrder).where(WorkOrder.work_order_id.isnot(None))
    if status_filter:
        q = q.where(WorkOrder.status == status_filter)
    if priority:
        q = q.where(WorkOrder.priority == priority)
    if asset:
        q = q.where(WorkOrder.asset.ilike(f"%{asset}%"))
    if from_date:
        q = q.where(WorkOrder.created_at >= from_date)
    if to_date:
        q = q.where(WorkOrder.created_at <= to_date)
    q = q.order_by(WorkOrder.created_at.desc()).offset((page - 1) * limit).limit(limit)
    try:
        result = await session.execute(q)
    except SQLAlchemyError as exc:
        log.error("work_order.list.db_error", exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    rows = result.scalars().all()
    log.debug("work_order.list.result", count=len(rows), page=page)
    return rows


@router.get("/filter/active",           response_model=List[WorkOrderResponse])
async def get_active(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(WorkOrder)
        .where(WorkOrder.work_order_id.isnot(None), WorkOrder.status == "active")
        .order_by(WorkOrder.created_at.desc())
    )
    return result.scalars().all()


@router.get("/filter/pending-approval", response_model=List[WorkOrderResponse])
async def get_pending(session: AsyncSession = Depends(get_session)):
    result = await session.execute(
        select(WorkOrder)
        .where(WorkOrder.work_order_id.isnot(None), WorkOrder.status == "pending_approval")
        .order_by(WorkOrder.created_at.desc())
    )
    return result.scalars().all()


# ── BE2-09: Status history ────────────────────────────────────────────────────

@router.get(
    "/{work_order_id}/history",
    response_model=List[StatusHistoryEntry],
    summary="Get status change history for a work order",
    description="Returns all status transitions for the given work order, oldest first.",
    responses=_404,
)
async def get_work_order_history(
    work_order_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_wo_or_404(work_order_id, session)   # 404 if not found
    try:
        result = await session.execute(
            select(StatusHistory)
            .where(StatusHistory.work_order_id == work_order_id)
            .order_by(StatusHistory.changed_at.asc())
        )
    except SQLAlchemyError as exc:
        raise DatabaseError(str(exc)) from exc
    return result.scalars().all()


# ── BE2-12: Bulk status update ────────────────────────────────────────────────

@router.patch(
    "/bulk/status",
    summary="Bulk status update",
    description=(
        "Applies the same status transition to multiple work orders. "
        "Each transition is validated against the state machine. "
        "Returns counts of successes and failures."
    ),
    responses=_422,
)
async def bulk_status_update(
    payload: BulkStatusUpdate,
    session: AsyncSession = Depends(get_session),
):
    if not payload.work_order_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "validation_error", "message": "work_order_ids must not be empty"},
        )
    log.info(
        "work_order.bulk_status.start",
        count=len(payload.work_order_ids),
        new_status=payload.new_status,
    )

    succeeded: list[str] = []
    failed:    list[dict] = []

    for wo_id in payload.work_order_ids:
        try:
            result = await session.execute(
                select(WorkOrder).where(WorkOrder.work_order_id == wo_id)
            )
            wo = result.scalar_one_or_none()
            if not wo:
                failed.append({"work_order_id": wo_id, "reason": "not_found"})
                continue

            allowed = _VALID_TRANSITIONS.get(wo.status, [])
            if payload.new_status not in allowed:
                failed.append({
                    "work_order_id": wo_id,
                    "reason": f"invalid_transition:{wo.status}->{payload.new_status}",
                })
                continue

            prev_status = wo.status
            wo.status = payload.new_status
            if payload.new_status == "preparing":
                wo.approved_at = datetime.now(timezone.utc)
            elif payload.new_status == "prepared":
                wo.prepared_at = datetime.now(timezone.utc)
            elif payload.new_status == "active":
                wo.sent_to_cmms_at = datetime.now(timezone.utc)

            await record_status_change(
                wo_id, prev_status, payload.new_status, session,
                notes=payload.notes,
            )
            await advance_journey_milestone(wo_id, payload.new_status, session)
            succeeded.append(wo_id)

        except SQLAlchemyError as exc:
            failed.append({"work_order_id": wo_id, "reason": str(exc)})

    try:
        await session.commit()
    except SQLAlchemyError as exc:
        await session.rollback()
        raise DatabaseError(str(exc)) from exc

    log.info(
        "work_order.bulk_status.complete",
        new_status=payload.new_status,
        updated=len(succeeded),
        failed=len(failed),
    )
    return {
        "updated": len(succeeded),
        "failed":  len(failed),
        "succeeded_ids": succeeded,
        "failed_details": failed,
    }


# ── Dynamic approval (register before bare /{work_order_id} where paths overlap) ─

class SuggestApprovalBody(BaseModel):
    work_type: str
    priority: str
    location_id: Optional[int] = None
    location: Optional[str] = None
    estimated_cost: Optional[float] = 0
    asset_category: Optional[str] = None
    work_order_id: Optional[str] = None


class ChainStepOverride(BaseModel):
    email: Optional[str] = None
    user_id: Optional[str] = None
    step: int


class CustomizeChainBody(BaseModel):
    chain: List[ChainStepOverride] = Field(..., min_length=1)


class RequestApprovalBody(BaseModel):
    approval_type: str = "preparation"


@router.post("/suggest-approval")
async def suggest_approval(body: SuggestApprovalBody):
    from ...core.logging import get_logger
    from ...services.dynamic_approval_engine import normalize_work_order_payload

    log = get_logger(__name__)
    payload = normalize_work_order_payload(body.model_dump())
    try:
        svc = ApprovalWorkflowService(aimms_api_url=settings.aimms_api_url)
        return await svc.suggest_chain(payload)
    except Exception as exc:
        log.error(
            "api.suggest_approval.failed",
            work_type=payload.get("work_type"),
            priority=payload.get("priority"),
            location=payload.get("location"),
            asset_category=payload.get("asset_category"),
            work_order_id=payload.get("work_order_id"),
            error=str(exc),
            exc_type=type(exc).__name__,
            exc_info=True,
        )
        raise


@router.get("/{work_order_id}/status-track")
async def get_work_order_status_track(
    work_order_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Full work order tracking: status, multi-step approval progress, technician,
    scheduling, blockers (parts/assets hold), status history, and journey.
    """
    from ...services.work_order_status_track import build_work_order_status_track

    track = await build_work_order_status_track(session, work_order_id)
    if not track.get("found"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "work_order_not_found", "message": f"Work order {work_order_id!r} not found"},
        )
    return track


@router.get("/{work_order_id}/approval-chain")
async def get_work_order_approval_chain(work_order_id: str):
    svc = ApprovalWorkflowService(aimms_api_url=settings.aimms_api_url)
    chain = await svc.get_approval_chain(work_order_id)
    return {"work_order_id": work_order_id, "chain": chain}


@router.patch("/{work_order_id}/customize-chain")
async def customize_work_order_approval_chain(
    work_order_id: str,
    body: CustomizeChainBody,
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import text
    for override in body.chain:
        email = override.email or override.user_id
        if not email:
            continue
        await session.execute(
            text("""
                UPDATE plenum_cafm.wo_approval_requests
                SET approver = :approver
                WHERE work_order_id = :wo_id AND step_order = :step AND status = 'pending'
            """),
            {"approver": email, "wo_id": work_order_id, "step": override.step},
        )
    await session.commit()
    return {"work_order_id": work_order_id, "customized_steps": len(body.chain)}


@router.post("/{work_order_id}/request-approval")
async def request_dynamic_approval(
    work_order_id: str,
    body: RequestApprovalBody = RequestApprovalBody(),
    session: AsyncSession = Depends(get_session),
):
    """Create multi-step approval requests from DynamicApprovalEngine suggestion."""
    wo = await _get_wo_or_404(work_order_id, session)
    svc = ApprovalWorkflowService(aimms_api_url=settings.aimms_api_url)
    wo_payload = {
        "work_order_id": wo.work_order_id,
        "priority": wo.priority,
        "work_type": wo.request_type,
        "request_type": wo.request_type,
        "location": wo.location,
        "asset": wo.asset,
        "asset_category": wo.asset_category or ((wo.asset or "").split()[0] if wo.asset else "general"),
        "estimated_cost": float(wo.estimated_cost or 0),
        "issue_description": wo.issue_description,
        "title": wo.title,
        "approval_type": wo.approval_type or body.approval_type,
    }
    return await svc.request_approval(wo_payload, approval_type=body.approval_type)


@router.post("/{work_order_id}/send-approval-email")
async def send_approval_email(
    work_order_id: str,
    step_order: int = 1,
    session: AsyncSession = Depends(get_session),
):
    """Send Outlook approval-request email to the approver for a chain step."""
    from ...services.approval_chain_service import send_approval_step_email

    result = await send_approval_step_email(session, work_order_id, step_order=step_order)
    await session.commit()
    return result


# ── Get / Update ──────────────────────────────────────────────────────────────

@router.get("/{work_order_id}", response_model=WorkOrderResponse, responses=_404)
async def get_work_order(work_order_id: str, session: AsyncSession = Depends(get_session)):
    return await _get_wo_or_404(work_order_id, session)


@router.patch("/{work_order_id}", response_model=WorkOrderResponse, responses=_404_422)
async def update_work_order(
    work_order_id: str,
    payload: WorkOrderUpdate,
    session: AsyncSession = Depends(get_session),
):
    wo = await _get_wo_or_404(work_order_id, session)
    updates = payload.model_dump(exclude_none=True)
    log.info("work_order.update.start", work_order_id=work_order_id, fields=list(updates.keys()))
    if wo.status == "pending_approval":
        deferred = {
            k
            for k in ("vendor", "scheduled_date", "scheduled_time", "estimated_duration")
            if k in updates
        }
        if deferred:
            log.info(
                "work_order.update.deferred_fields_ignored",
                work_order_id=work_order_id,
                fields=sorted(deferred),
            )
            for key in deferred:
                updates.pop(key, None)
    for field, value in updates.items():
        setattr(wo, field, value)
    try:
        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.update.db_error", work_order_id=work_order_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    log.info("work_order.updated", work_order_id=work_order_id, fields=list(updates.keys()))
    return wo


# ── Status transition ─────────────────────────────────────────────────────────

@router.patch(
    "/{work_order_id}/status",
    response_model=WorkOrderResponse,
    responses=_404_422,
)
async def update_status(
    work_order_id: str,
    payload: StatusUpdate,
    session: AsyncSession = Depends(get_session),
):
    wo = await _get_wo_or_404(work_order_id, session)
    allowed = _VALID_TRANSITIONS.get(wo.status, [])
    if payload.new_status not in allowed:
        log.warning(
            "work_order.status.invalid_transition",
            work_order_id=work_order_id,
            from_status=wo.status,
            requested=payload.new_status,
            allowed=allowed,
        )
        raise InvalidStatusTransition(wo.status, payload.new_status, allowed)

    prev_status = wo.status
    log.info(
        "work_order.status.transition",
        work_order_id=work_order_id,
        from_status=prev_status,
        to_status=payload.new_status,
    )
    wo.status = payload.new_status
    if payload.new_status == "preparing":
        wo.approved_at = datetime.now(timezone.utc)
    elif payload.new_status == "prepared":
        wo.prepared_at = datetime.now(timezone.utc)
    elif payload.new_status == "active":
        wo.sent_to_cmms_at = datetime.now(timezone.utc)

    await record_status_change(work_order_id=work_order_id, from_status=prev_status,
                               to_status=payload.new_status, session=session,
                               notes=payload.notes)
    await advance_journey_milestone(work_order_id, payload.new_status, session)

    try:
        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.status.db_error", work_order_id=work_order_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc
    return wo


# ── Approve / Close ───────────────────────────────────────────────────────────

@router.post(
    "/{work_order_id}/approve",
    response_model=WorkOrderResponse,
    responses={**_404, 409: {"description": "Work order is not pending approval"}},
)
async def approve_work_order(
    work_order_id: str,
    session: AsyncSession = Depends(get_session),
):
    log.info("work_order.approve.start", work_order_id=work_order_id)
    wo = await _get_wo_or_404(work_order_id, session)
    if wo.status != "pending_approval":
        log.warning(
            "work_order.approve.not_pending",
            work_order_id=work_order_id,
            current_status=wo.status,
        )
        raise ApprovalNotPending(work_order_id, wo.status)
    wo.status = "preparing"
    wo.approved_at = datetime.now(timezone.utc)

    await record_status_change(work_order_id, "pending_approval", "preparing", session)
    await advance_journey_milestone(work_order_id, "preparing", session)

    try:
        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.approve.db_error", work_order_id=work_order_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    log.info("work_order.approved", work_order_id=work_order_id)
    return wo


@router.post(
    "/{work_order_id}/close",
    response_model=WorkOrderResponse,
    responses={**_404, 409: {"description": "Work order is already closed"}},
)
async def close_work_order(
    work_order_id: str,
    session: AsyncSession = Depends(get_session),
):
    log.info("work_order.close.start", work_order_id=work_order_id)
    wo = await _get_wo_or_404(work_order_id, session)
    if wo.status == "closed":
        log.warning("work_order.close.already_closed", work_order_id=work_order_id)
        raise WorkOrderAlreadyClosed(work_order_id)
    prev = wo.status
    wo.status = "closed"

    await record_status_change(work_order_id, prev, "closed", session)
    await advance_journey_milestone(work_order_id, "closed", session)

    try:
        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.close.db_error", work_order_id=work_order_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    log.info("work_order.closed", work_order_id=work_order_id, from_status=prev)
    return wo


@router.post("/{work_order_id}/prepare", response_model=WorkOrderResponse, responses=_404_422)
async def prepare_work_order(
    work_order_id: str,
    payload: WorkOrderUpdate,
    session: AsyncSession = Depends(get_session),
):
    log.info("work_order.prepare.start", work_order_id=work_order_id)
    wo = await _get_wo_or_404(work_order_id, session)
    allowed = _VALID_TRANSITIONS.get(wo.status, [])
    if "prepared" not in allowed:
        log.warning(
            "work_order.prepare.invalid_transition",
            work_order_id=work_order_id,
            current_status=wo.status,
        )
        raise InvalidStatusTransition(wo.status, "prepared", allowed)

    fields_set = list(payload.model_dump(exclude_none=True).keys())
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(wo, field, value)
    prev = wo.status
    wo.status = "prepared"
    wo.prepared_at = datetime.now(timezone.utc)

    await record_status_change(work_order_id, prev, "prepared", session)
    await advance_journey_milestone(work_order_id, "prepared", session)

    try:
        await session.commit()
        await session.refresh(wo)
    except SQLAlchemyError as exc:
        await session.rollback()
        log.error("work_order.prepare.db_error", work_order_id=work_order_id, exc_info=exc)
        raise DatabaseError(str(exc)) from exc

    log.info("work_order.prepared", work_order_id=work_order_id, fields_set=fields_set)
    return wo
