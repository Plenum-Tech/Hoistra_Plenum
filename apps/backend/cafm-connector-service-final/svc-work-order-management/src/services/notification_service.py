"""Email notification service — sends via Outlook Graph API with GPT-rendered HTML."""
from typing import Dict, Any, List

from ..integrations.outlook_connector import OutlookConnector
from ..integrations.workspace_connector import WorkspaceConnector
from ..core.logging import get_logger
from ..config import settings
from .email_renderer import render_email_html

log = get_logger(__name__)


class NotificationService:
    def __init__(
        self,
        connector: OutlookConnector,
        api_key: str = "",
        model: str = "gpt-4o-mini",
    ):
        self.outlook = connector
        self._api_key = api_key
        self._model = model

    async def _render(self, email_type: str, context: Dict[str, Any]) -> str:
        return await render_email_html(email_type, context, self._api_key, self._model)

    # ── Outbound emails ──────────────────────────────────────────────────────

    async def send_email(self, to: str, subject: str, body: str) -> None:
        await self.outlook.send_email(to=to, subject=subject, body=body)

    async def send_missing_info_email(
        self,
        original_email: Dict[str, Any],
        missing_fields: List[str],
    ) -> None:
        """Reply to the requester asking for the fields OpenAI couldn't extract."""
        to = original_email["from"]
        log.info(
            "notification.missing_info.send",
            to=to,
            email_id=original_email.get("id"),
            missing_fields=missing_fields,
        )
        _labels = {
            "asset":             "Asset name or equipment ID (e.g. HVAC Unit #3, Lift-02)",
            "location":          "Location (e.g. Building A, Floor 3, Room 305)",
            "issue_description": "Detailed description of the issue",
            "requester_name":    "Your full name",
            "requester_email":   "Your email address",
        }
        context = {
            "from_name":      original_email.get("from_name") or "Requester",
            "missing_fields": [{"key": f, "label": _labels.get(f, f)} for f in missing_fields],
            "original_subject": original_email.get("subject", ""),
            "received_at":    original_email.get("received_at", ""),
        }
        html = await self._render("missing_info", context)
        await self.outlook.send_email(
            to=to,
            subject=f"Re: {original_email.get('subject', '')} — Additional Info Needed",
            body=html,
            reply_to_id=original_email.get("id"),
            is_html=True,
        )
        log.info("notification.missing_info.sent", to=to, email_id=original_email.get("id"))

    async def send_wo_created_confirmation(
        self,
        work_order_id: str,
        priority: str,
        asset: str,
        location: str,
        requester_email: str,
        requester_name: str,
        original_email_id: str | None = None,
    ) -> None:
        """Confirm to the requester that their WO was created."""
        log.info(
            "notification.confirmation.send",
            to=requester_email,
            work_order_id=work_order_id,
            priority=priority,
        )
        context = {
            "requester_name": requester_name,
            "work_order_id":  work_order_id,
            "asset":          asset,
            "location":       location,
            "priority":       priority.upper(),
            "status":         "Pending Approval",
        }
        html = await self._render("wo_created", context)
        await self.outlook.send_email(
            to=requester_email,
            subject=f"Work Order Created: {work_order_id}",
            body=html,
            reply_to_id=original_email_id,
            is_html=True,
        )
        log.info("notification.confirmation.sent", to=requester_email, work_order_id=work_order_id)

    async def send_approval_request(
        self,
        work_order_id: str,
        asset: str,
        location: str,
        issue_description: str,
        priority: str,
        approver_email: str,
        approver_name: str,
        approver_role: str = "",
        requester_name: str = "",
        assessment_summary: dict | None = None,
    ) -> None:
        """
        Ask the approver to approve or reject by replying to this email.
        The subject embeds the WO ID so the poll can detect the reply.
        """
        log.info(
            "notification.approval_request.send",
            to=approver_email,
            work_order_id=work_order_id,
            priority=priority,
        )
        context = {
            "approver_name":      approver_name,
            "approver_email":     approver_email,
            "approver_role":      approver_role or "—",
            "work_order_id":      work_order_id,
            "requester_name":     requester_name or "—",
            "asset":              asset,
            "location":           location,
            "issue_description":  issue_description,
            "priority":           priority.upper(),
            "assessment_summary": assessment_summary or {},
        }
        html = await self._render("approval_request", context)
        await self.outlook.send_email(
            to=approver_email,
            subject=f"[APPROVAL REQUIRED] {work_order_id} — {asset} — {priority.upper()} priority",
            body=html,
            is_html=True,
        )
        log.info("notification.approval_request.sent", to=approver_email, work_order_id=work_order_id)

    async def send_approval_confirmed(
        self,
        work_order_id: str,
        requester_name: str,
        requester_email: str,
        asset: str,
        location: str,
        priority: str,
        approver_name: str,
        technician: dict | None = None,
        ppm_info: dict | None = None,
        scheduled_date: str | None = None,
        scheduled_time: str | None = None,
        estimated_duration: float | None = None,
    ) -> None:
        """Tell the requester their WO has been approved and a technician assigned."""
        log.info(
            "notification.approval_confirmed.send",
            to=requester_email,
            work_order_id=work_order_id,
        )
        context = {
            "requester_name": requester_name,
            "work_order_id":  work_order_id,
            "asset":          asset,
            "location":       location,
            "priority":       priority.upper(),
            "approver_name":  approver_name,
            "status":         "Approved — In Preparation",
            "technician":     technician or {},
            "ppm_info":       ppm_info or {},
            "scheduled_date": scheduled_date,
            "scheduled_time": scheduled_time,
            "estimated_duration": estimated_duration,
        }
        html = await self._render("approval_confirmed", context)
        await self.outlook.send_email(
            to=requester_email,
            subject=f"Work Order Approved: {work_order_id}",
            body=html,
            is_html=True,
        )
        log.info("notification.approval_confirmed.sent", to=requester_email, work_order_id=work_order_id)

    async def send_technician_assignment(
        self,
        work_order_id: str,
        technician_name: str,
        technician_email: str,
        asset: str,
        location: str,
        priority: str,
        issue_description: str,
        ppm_info: dict | None = None,
        scheduled_date: str | None = None,
        scheduled_time: str | None = None,
        estimated_duration: float | None = None,
    ) -> None:
        """Notify the assigned technician of a new work order."""
        log.info(
            "notification.technician_assignment.send",
            to=technician_email,
            work_order_id=work_order_id,
        )
        context = {
            "technician_name":   technician_name,
            "work_order_id":     work_order_id,
            "asset":             asset,
            "location":          location,
            "priority":          priority.upper(),
            "issue_description": issue_description,
            "ppm_info":          ppm_info or {},
            "scheduled_date":    scheduled_date,
            "scheduled_time":    scheduled_time,
            "estimated_duration": estimated_duration,
        }
        html = await self._render("technician_assignment", context)
        await self.outlook.send_email(
            to=technician_email,
            subject=f"[ASSIGNED] Work Order {work_order_id} — {asset} — {priority.upper()}",
            body=html,
            is_html=True,
        )
        log.info("notification.technician_assigned.sent",
                 to=technician_email, work_order_id=work_order_id)

    async def send_rejection_notice(
        self,
        work_order_id: str,
        requester_name: str,
        requester_email: str,
        asset: str,
        approver_name: str,
        rejection_notes: str = "",
    ) -> None:
        """Inform the requester their WO was rejected."""
        log.info(
            "notification.rejection.send",
            to=requester_email,
            work_order_id=work_order_id,
        )
        context = {
            "requester_name":  requester_name,
            "work_order_id":   work_order_id,
            "asset":           asset,
            "approver_name":   approver_name,
            "status":          "Not Approved",
            "rejection_notes": rejection_notes,
        }
        html = await self._render("rejection", context)
        await self.outlook.send_email(
            to=requester_email,
            subject=f"Work Order Not Approved: {work_order_id}",
            body=html,
            is_html=True,
        )
        log.info("notification.rejection.sent", to=requester_email, work_order_id=work_order_id)

    async def send_workspace_notification(self, notification: Dict[str, Any]) -> None:
        """Send a workspace-level notification to AIMMS workspace service."""
        connector = WorkspaceConnector(settings.aimms_api_url, settings.aimms_api_key)
        log.info("notification.workspace.send", work_order_id=notification.get("work_order_id"))
        await connector.send_notification(notification)
        log.info("notification.workspace.sent", work_order_id=notification.get("work_order_id"))
