"""8-step email-based work order processing — see docs/WORK_ORDER_MODULE_COMPLETE.md."""
from typing import Dict, Any, List, Optional
from datetime import datetime
import asyncio
from openai import OpenAI
from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models.work_order import WorkOrder
from ..services.approval_chain_service import (
    create_approval_requests_from_suggestion,
    suggest_approval_for_work_order,
)
from ..services.work_order_flow import WorkOrderFlow
from ..services.approval_workflow import ApprovalWorkflowService
from ..services.notification_service import NotificationService
from ..integrations.outlook_connector import OutlookConnector
from ..core.logging import get_logger
from ..config import settings

log = get_logger(__name__)


class EmailWorkOrderProcessor:
    def __init__(
        self,
        aimms_api_url: str,
        outlook_api_url: str,
        cmms_api_url: str,
        openai_api_key: str,
        model: str = "gpt-4o-mini",
    ):
        self.aimms_api_url = aimms_api_url
        self.outlook_api_url = outlook_api_url
        self.cmms_api_url = cmms_api_url
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.model = model

    async def process_incoming_email(self, email_id: str) -> Dict[str, Any]:
        log.info("email_processor.process.start", email_id=email_id)
        email = await self.get_email_from_outlook(email_id)
        extracted = await self.extract_work_order_details(email)
        missing = self.identify_missing_info(extracted)
        if missing:
            await self.send_missing_info_email(email, missing)
            return {"status": "awaiting_info", "email_id": email_id, "missing_fields": missing}

        work_order = await self.create_work_order_from_email(email, extracted)
        approval_req = await self.request_approval(work_order)
        return {"status": "pending_approval", "work_order_id": work_order.get("work_order_id"), "approval_request_id": approval_req.get("request_id")}

    async def extract_work_order_details(self, email: Dict[str, Any]) -> Dict[str, Any]:
        flow = WorkOrderFlow(api_key=settings.openai_api_key, model=settings.openai_model)
        parsed = await asyncio.to_thread(flow.email_parser.parse, email)  # type: ignore[attr-defined]
        return parsed.get("data", {})

    def identify_missing_info(self, extracted_data: Dict[str, Any]) -> List[str]:
        required = ["asset", "location", "issue_description", "requester_name", "requester_email"]
        return [f for f in required if not extracted_data.get(f)]

    async def send_missing_info_email(self, original_email: Dict, missing_fields: List[str]) -> None:
        if not (settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret):
            log.warning("email_processor.missing_info.skip_no_outlook", email_id=original_email.get("id"))
            return
        connector = OutlookConnector(
            tenant_id=settings.azure_tenant_id,
            client_id=settings.azure_client_id,
            client_secret=settings.azure_client_secret,
            user_email=settings.outlook_user_email,
        )
        notifier = NotificationService(connector)
        await notifier.send_missing_info_email(original_email, missing_fields)
        log.info("email_processor.missing_info.sent", email_id=original_email.get("id"), missing_fields=missing_fields)

    async def create_work_order_from_email(
        self, email: Dict, extracted_data: Dict
    ) -> Dict[str, Any]:
        flow = WorkOrderFlow(api_key=settings.openai_api_key, model=settings.openai_model)
        async with AsyncSessionLocal() as session:
            result = await flow.create_from_email(email, session)
        return result

    async def request_approval(self, work_order: Dict) -> Dict[str, Any]:
        wo_id = work_order.get("work_order_id")
        if not wo_id:
            raise ValueError("work_order_id missing while creating approval request")
        async with AsyncSessionLocal() as session:
            suggestion = await suggest_approval_for_work_order(session, work_order)
            result = await create_approval_requests_from_suggestion(
                session,
                wo_id,
                work_order.get("approval_type") or "preparation",
                suggestion,
                work_order_context=work_order,
            )
            await session.commit()
        log.info(
            "email_processor.approval.requested",
            work_order_id=wo_id,
            confidence=result.get("confidence"),
            steps=len(result.get("approval_requests") or []),
        )
        return result

    async def handle_approval_response(
        self,
        approval_request_id: str,
        approved: bool,
        approver_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        svc = ApprovalWorkflowService(aimms_api_url=self.aimms_api_url)
        return await svc.handle_approval_response(
            approval_request_id, approved, notes=approver_notes
        )

    async def complete_preparation(
        self, work_order_id: str, preparation_data: Dict
    ) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(WorkOrder).where(WorkOrder.work_order_id == work_order_id))
            wo = result.scalar_one_or_none()
            if not wo:
                return {"status": "not_found", "work_order_id": work_order_id}
            for field in ["vendor", "manpower", "scheduled_date", "scheduled_time", "estimated_duration", "special_requirements"]:
                if field in preparation_data:
                    setattr(wo, field, preparation_data[field])
            wo.status = "prepared"
            wo.prepared_at = datetime.utcnow()
            await session.commit()
            return {"status": "prepared", "work_order_id": work_order_id}

    async def handle_final_approval(
        self, approval_request_id: str, approved: bool
    ) -> Dict[str, Any]:
        result = await self.handle_approval_response(approval_request_id, approved, approver_notes="final_approval")
        if result.get("status") != "processed":
            return result
        if approved:
            result["next_step"] = "ready_for_cmms"
        else:
            result["next_step"] = "closed"
        return result

    async def get_email_from_outlook(self, email_id: str) -> Dict[str, Any]:
        if not settings.outlook_access_token:
            raise ValueError("OUTLOOK_ACCESS_TOKEN not configured")
        connector = OutlookConnector(settings.outlook_access_token, settings.outlook_user_email)
        return await connector.get_email(email_id)
