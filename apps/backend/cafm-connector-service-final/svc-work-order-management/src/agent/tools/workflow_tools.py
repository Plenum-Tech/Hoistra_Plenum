"""Workflow tools: create work order, request approval, submit to CMMS."""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.work_order import WorkOrder
from ...services.approval_chain_service import (
    approval_suggestion_after_create,
    create_approval_requests_from_suggestion,
    suggest_approval_for_work_order,
    work_order_payload_from_model,
)
from ...services.cmms_integration import CMMSIntegrationService
from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)


class WorkflowTools:
    def __init__(self, session: AsyncSession, session_id: str) -> None:
        self.session = session
        self.session_id = session_id
        self._cmms = CMMSIntegrationService(
            cmms_api_url=settings.cmms_api_url,
            cmms_api_key=settings.cmms_api_key,
        )

    async def create_work_order(
        self,
        source: str,
        asset: str,
        location: str,
        issue_description: str,
        priority: str,
        request_type: str,
        requester_name: Optional[str] = None,
        requester_email: Optional[str] = None,
        requester_phone: Optional[str] = None,
        vendor: Optional[str] = None,
        scheduled_date: Optional[str] = None,
        scheduled_time: Optional[str] = None,
        estimated_duration: Optional[float] = None,
        special_requirements: Optional[str] = None,
        submit_to_cmms: bool = True,
    ) -> Dict[str, Any]:
        wo_id = f"WO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"

        # Derive approval_type from source
        approval_map = {
            "email": "preparation",
            "tenant": "preparation",
            "ppm": "simple",
            "ppm_schedule": "simple",
            "internal": "simple",
            "manual": "full",
            "chat": "preparation",
            "remediation": "full",
        }
        approval_type = approval_map.get(source, "preparation")

        wo = WorkOrder(
            work_order_id=wo_id,
            organization_id=int(settings.default_organization_id),
            title=issue_description[:255],
            source=source,
            asset=asset,
            location=location,
            issue_description=issue_description,
            priority=priority,
            request_type=request_type,
            status="pending_approval",
            approval_type=approval_type,
            requester_name=requester_name,
            requester_email=requester_email,
            requester_phone=requester_phone,
            # Vendor/scheduling/assignment are deferred until final approval.
            vendor=None,
            scheduled_date=None,
            scheduled_time=None,
            estimated_duration=None,
            special_requirements=special_requirements,
        )

        self.session.add(wo)
        await self.session.flush()  # get PK without final commit

        cmms_result: Dict[str, Any] = {}
        if submit_to_cmms and settings.cmms_integration_enabled:
            cmms_result = await self._cmms.send_work_order(
                {
                    "work_order_id": wo_id,
                    "issue_description": issue_description,
                    "asset": asset,
                    "location": location,
                    "priority": priority,
                    "request_type": request_type,
                    "scheduled_date": None,
                    "vendor": None,
                    "estimated_duration": 0,
                }
            )
            if cmms_result.get("cmms_wo_id"):
                wo.cmms_work_order_id = cmms_result["cmms_wo_id"]
                wo.sent_to_cmms_at = datetime.now(timezone.utc)

        await self.session.commit()

        approval_preview = await approval_suggestion_after_create(self.session, wo)
        auto = approval_preview.get("auto_suggestion") or {}

        log.info(
            "tool.create_work_order.done",
            wo_id=wo_id,
            source=source,
            priority=priority,
            session_id=self.session_id,
            approval_confidence=approval_preview.get("confidence"),
        )

        base_message = (
            f"Work order **{wo_id}** created successfully. "
            f"Status: pending_approval | Priority: {priority}. "
            "Scheduling and vendor assignment will be set after final approval."
        )
        if auto.get("message"):
            base_message += f"\n\n{auto['message']}"

        return {
            "success": True,
            "work_order_id": wo_id,
            "status": "pending_approval",
            "approval_type": approval_type,
            "priority": priority,
            "scheduled_date": None,
            "vendor": None,
            "cmms_submitted": bool(cmms_result.get("success")),
            "cmms_wo_id": cmms_result.get("cmms_wo_id"),
            "approval_suggestion": approval_preview,
            "auto_suggestion": auto,
            "message": base_message,
        }

    async def suggest_approval_chain(
        self,
        work_type: str,
        priority: str,
        location_id: Optional[int] = None,
        estimated_cost: Optional[float] = None,
        asset_category: Optional[str] = None,
        location: Optional[str] = None,
        work_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Preview dynamic approval chain (no rows committed)."""
        wo_payload = {
            "work_type": work_type,
            "priority": priority,
            "location_id": location_id,
            "location": location or (str(location_id) if location_id else ""),
            "estimated_cost": estimated_cost or 0,
            "asset_category": asset_category or work_type,
            "work_order_id": work_order_id,
        }
        suggestion = await suggest_approval_for_work_order(
            self.session, wo_payload, persist_suggestion=False
        )
        auto = suggestion.get("auto_suggestion") or {}
        return {
            "chain": auto.get("recommended_chain_summary")
            or " → ".join(
                f"{s['name']} ({s['role']})" for s in suggestion.get("chain", [])
            )
            or "(no approvers resolved)",
            "chain_detail": suggestion.get("chain"),
            "confidence": suggestion.get("confidence"),
            "confidence_label": auto.get("confidence_label"),
            "reason": suggestion.get("reason"),
            "match_score": suggestion.get("match_score"),
            "risk_score": suggestion.get("risk_score"),
            "historical_matches": suggestion.get("historical_matches"),
            "previous_approval_processes": suggestion.get("previous_approval_processes"),
            "historical_alternative_chain": suggestion.get("historical_alternative_chain"),
            "follow_pattern_from": suggestion.get("based_on_work_order_id"),
            "auto_suggestion": auto,
            "message": auto.get("message")
            or suggestion.get("reason"),
        }

    async def request_approval(
        self,
        work_order_id: str,
        approval_type: str,
        approver: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = await self.session.execute(
            select(WorkOrder).where(WorkOrder.work_order_id == work_order_id)
        )
        wo = result.scalar_one_or_none()
        if not wo:
            return {"success": False, "error": f"Work order {work_order_id} not found"}

        wo_payload = work_order_payload_from_model(wo)
        suggestion = await suggest_approval_for_work_order(self.session, wo_payload)
        if approver and suggestion.get("chain"):
            suggestion["chain"][0]["email"] = approver
            suggestion["chain"][0]["name"] = approver

        out = await create_approval_requests_from_suggestion(
            self.session,
            work_order_id,
            approval_type,
            suggestion,
            work_order_context=wo_payload,
        )
        await self.session.commit()

        log.info(
            "tool.request_approval.done",
            work_order_id=work_order_id,
            approval_type=approval_type,
            steps=len(suggestion.get("chain") or []),
        )
        return out

    async def send_approval_request_email(
        self,
        work_order_id: str,
        step_order: int = 1,
    ) -> Dict[str, Any]:
        from ...services.approval_chain_service import send_approval_step_email

        out = await send_approval_step_email(
            self.session, work_order_id, step_order=step_order
        )
        await self.session.commit()
        log.info(
            "tool.send_approval_request_email.done",
            work_order_id=work_order_id,
            step_order=step_order,
            email_sent=out.get("email_sent"),
        )
        return out

    async def get_work_order_status_track(self, work_order_id: str) -> Dict[str, Any]:
        from ...services.work_order_status_track import build_work_order_status_track

        track = await build_work_order_status_track(self.session, work_order_id)
        log.info(
            "tool.get_work_order_status_track.done",
            work_order_id=work_order_id,
            found=track.get("found"),
        )
        return track
