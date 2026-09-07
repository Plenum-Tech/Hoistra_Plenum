"""Approval routing and multi-step approval workflow."""
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from sqlalchemy import select, text
from ..db import AsyncSessionLocal
from ..models.approval import ApprovalRequest
from ..models.work_order import WorkOrder
from ..services.journey_service import record_status_change, advance_journey_milestone
from ..services.dynamic_approval_engine import DynamicApprovalEngine
from ..services.approval_chain_service import (
    create_approval_requests_from_suggestion,
    suggest_approval_for_work_order,
    _notify_step,
)
from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


class ApprovalWorkflowService:
    def __init__(self, aimms_api_url: str):
        self.aimms_api_url = aimms_api_url or settings.aimms_api_url
        self._engine = DynamicApprovalEngine(aimms_api_url=self.aimms_api_url)

    async def suggest_chain(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        """Preview approval chain (no approval rows written)."""
        try:
            async with AsyncSessionLocal() as session:
                return await self._engine.suggest_chain(
                    session,
                    work_order,
                    persist=bool(work_order.get("work_order_id")),
                )
        except Exception as exc:
            log.error(
                "approval.suggest_chain.failed",
                work_order_id=work_order.get("work_order_id"),
                work_type=work_order.get("work_type"),
                priority=work_order.get("priority"),
                location=work_order.get("location"),
                error=str(exc),
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            raise

    async def determine_approver(self, work_order: Dict) -> str:
        """
        Backward-compatible single approver lookup — first step of dynamic chain,
        or legacy email fallback.
        """
        async with AsyncSessionLocal() as session:
            suggestion = await self._engine.suggest_chain(session, work_order, persist=False)
        chain = suggestion.get("chain") or []
        if chain:
            return chain[0].get("email") or chain[0].get("name") or "facilities.manager@aimms.local"
        priority = (work_order.get("priority") or "medium").lower()
        if priority in ("critical", "urgent"):
            return "duty.manager@aimms.local"
        if priority == "high":
            return "facilities.manager@aimms.local"
        return "supervisor@aimms.local"

    async def request_approval(
        self,
        work_order: Dict[str, Any],
        approval_type: str = "preparation",
    ) -> Dict[str, Any]:
        """Create multi-step approval requests from dynamic engine suggestion."""
        wo_id = work_order.get("work_order_id")
        if not wo_id:
            raise ValueError("work_order_id required")

        async with AsyncSessionLocal() as session:
            suggestion = await suggest_approval_for_work_order(session, work_order)
            result = await create_approval_requests_from_suggestion(
                session,
                wo_id,
                approval_type,
                suggestion,
                work_order_context=work_order,
            )
            await session.commit()
        return result

    async def handle_approval_response(
        self,
        approval_request_id: str,
        approved: bool,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle approve/reject with sequential chain unblock."""
        log.info("approval.response.start", approval_request_id=approval_request_id, approved=approved)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.request_id == approval_request_id)
            )
            req = result.scalar_one_or_none()
            if not req:
                log.warning("approval.response.not_found", approval_request_id=approval_request_id)
                return {"status": "not_found", "approval_request_id": approval_request_id}
            if req.status != "pending":
                return {
                    "status": "already_processed",
                    "approval_request_id": approval_request_id,
                    "current_status": req.status,
                }

            wo_result = await session.execute(
                select(WorkOrder).where(WorkOrder.work_order_id == req.work_order_id)
            )
            wo = wo_result.scalar_one_or_none()
            if not wo:
                return {"status": "work_order_missing", "approval_request_id": approval_request_id}

            if not approved:
                req.status = "rejected"
                req.notes = notes
                req.responded_at = datetime.now(timezone.utc)
                prev_status = wo.status
                wo.status = "closed"
                await record_status_change(
                    req.work_order_id, prev_status, wo.status, session, notes=notes
                )
                await advance_journey_milestone(req.work_order_id, wo.status, session)
                await session.execute(
                    text("""
                        UPDATE plenum_cafm.wo_approval_requests
                        SET status = 'rejected', responded_at = NOW()
                        WHERE work_order_id = :wo_id AND status = 'pending'
                    """),
                    {"wo_id": req.work_order_id},
                )
                await session.commit()
                return {
                    "status": "rejected",
                    "work_order_id": req.work_order_id,
                    "approval_request_id": approval_request_id,
                }

            req.status = "approved"
            req.notes = notes
            req.responded_at = datetime.now(timezone.utc)

            current_step = int(req.step_order or req.level or 1)
            next_row = await session.execute(
                text("""
                    SELECT request_id, approver, step_order, level
                    FROM plenum_cafm.wo_approval_requests
                    WHERE work_order_id = :wo_id
                      AND status = 'pending'
                      AND COALESCE(step_order, level, 1) = :next_step
                    LIMIT 1
                """),
                {"wo_id": req.work_order_id, "next_step": current_step + 1},
            )
            next_step = next_row.fetchone()

            if next_step:
                now = datetime.now(timezone.utc)
                await session.execute(
                    text("""
                        UPDATE plenum_cafm.wo_approval_requests
                        SET unblocked_at = :now
                        WHERE request_id = :rid
                    """),
                    {"now": now, "rid": next_step.request_id},
                )
                wo_context = {
                    "work_order_id": wo.work_order_id,
                    "asset": wo.asset,
                    "location": wo.location,
                    "priority": wo.priority,
                    "issue_description": wo.issue_description,
                    "title": wo.title,
                }
                next_step_num = next_step.step_order or next_step.level or (current_step + 1)
                await _notify_step(
                    session,
                    {
                        "request_id": next_step.request_id,
                        "approver": next_step.approver,
                        "step": int(next_step_num),
                    },
                    wo_context,
                )
                await session.commit()
                return {
                    "status": "approved_step_complete",
                    "work_order_id": req.work_order_id,
                    "approval_request_id": approval_request_id,
                    "next_approver": next_step.approver,
                    "next_request_id": next_step.request_id,
                }

            # Lazy import avoids circular dependency with approval_processor.
            from .approval_processor import apply_final_approval_finalization  # noqa: PLC0415

            return await apply_final_approval_finalization(
                session,
                wo,
                notes=notes,
                approval_request_id=approval_request_id,
                approver_name=notes or "Approver",
            )

    async def send_approval_notification(
        self, approval_request: Dict, work_order: Dict
    ) -> None:
        log.info(
            "approval.notification.dispatch",
            approval_request_id=approval_request.get("request_id"),
            work_order_id=work_order.get("work_order_id"),
            approver=approval_request.get("approver"),
        )

    async def get_approval_chain(self, work_order_id: str) -> list[Dict[str, Any]]:
        from ..services.dynamic_approval_engine import DynamicApprovalEngine

        async with AsyncSessionLocal() as session:
            caps = await DynamicApprovalEngine._capabilities(session)
            ar_order = DynamicApprovalEngine._approval_step_order_sql(caps, alias="ar")
            optional: list[str] = []
            if caps.get("ar_step_order"):
                optional.append("ar.step_order")
            if caps.get("ar_level"):
                optional.append("ar.level")
            if caps.get("ar_step_order"):
                optional.extend(
                    ["ar.unblocked_at", "ar.risk_score", "ar.match_score", "ar.suggestion_source"]
                )
            opt_sql = ", ".join(optional) + "," if optional else ""
            rows = await session.execute(
                text(f"""
                    SELECT ar.request_id, ar.approver, ar.status, {opt_sql}
                           ar.requested_at, ar.responded_at
                    FROM plenum_cafm.wo_approval_requests ar
                    WHERE ar.work_order_id = :wo_id
                    ORDER BY {ar_order} ASC
                """),
                {"wo_id": work_order_id},
            )
            return [dict(r._mapping) for r in rows.fetchall()]
