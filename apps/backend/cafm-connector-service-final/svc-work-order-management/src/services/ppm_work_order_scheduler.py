"""Automated PPM work order scheduler — queries plenum_cafm.maintenance_plans."""
from typing import Dict, Any, List
from datetime import datetime, timezone, date
import asyncio

from sqlalchemy import select

from ..db import AsyncSessionLocal
from ..models.ppm_schedule import PPMSchedule
from ..models.work_order import WorkOrder
from ..config import settings
from ..core.logging import get_logger
from .journey_service import create_journey_for_work_order, record_status_change
from .notification_service import NotificationService
from ..integrations.outlook_connector import OutlookConnector

log = get_logger(__name__)


def _wo_id() -> str:
    return f"WO-PPM-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:22]}"


class PPMWorkOrderScheduler:
    def __init__(self, aimms_api_url: str):
        self.aimms_api_url = aimms_api_url

    async def run_scheduler(self) -> None:
        log.info("ppm_scheduler.loop.start")
        while True:
            try:
                due = await self.get_due_ppm_schedules()
                log.info("ppm_scheduler.loop.tick", due_count=len(due))
                for schedule in due:
                    await self.create_ppm_work_order(schedule)
            except Exception as exc:
                log.error("ppm_scheduler.loop.error", exc_info=exc)
            await asyncio.sleep(3600)

    async def get_due_ppm_schedules(self) -> List[Dict[str, Any]]:
        log.debug("ppm_scheduler.fetch.start")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PPMSchedule).where(PPMSchedule.status == "active")
            )
            schedules = result.scalars().all()

        due = []
        for s in schedules:
            row = {
                "schedule_id": str(s.schedule_id),
                "asset_id": str(s.asset_id) if s.asset_id else None,
                "task_description": s.description,
                "task_type": s.maintenance_type,
                "frequency_type": s.frequency_type,
                "frequency_value": s.frequency_value,
                "next_due_date": s.next_due_date.isoformat() if s.next_due_date else None,
                "priority": "medium",
            }
            if self.is_schedule_due(row):
                due.append(row)

        log.info("ppm_scheduler.fetch.complete", total_active=len(schedules), due_count=len(due))
        return due

    def is_schedule_due(self, schedule: Dict[str, Any]) -> bool:
        # Primary: check next_due_date if available
        next_due = schedule.get("next_due_date")
        if next_due is not None:
            if isinstance(next_due, str):
                next_due = date.fromisoformat(next_due)
            return next_due <= date.today()

        # Fallback: legacy frequency + last_executed logic
        frequency = schedule.get("frequency")
        last_executed = schedule.get("last_executed")
        if not last_executed:
            return True
        delta = (datetime.utcnow() - datetime.fromisoformat(last_executed)).days
        return {
            "daily": delta >= 1,
            "weekly": delta >= 7,
            "monthly": delta >= 30,
            "quarterly": delta >= 90,
            "annually": delta >= 365,
        }.get(frequency, False)

    async def create_ppm_work_order(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        wo_id = _wo_id()
        org_id = None
        if settings.default_organization_id:
            try:
                org_id = int(settings.default_organization_id)
            except (TypeError, ValueError):
                pass

        title = schedule.get("task_description") or "PPM Work Order"
        priority = schedule.get("priority", "medium")

        async with AsyncSessionLocal() as session:
            wo = WorkOrder(
                work_order_id=wo_id,
                organization_id=org_id,
                title=title,
                source="ppm",
                source_reference=schedule.get("schedule_id"),
                asset=str(schedule["asset_id"]) if schedule.get("asset_id") else None,
                task_description=schedule.get("task_description"),
                issue_description=title,
                priority=priority,
                request_type="maintenance",
                status="pending_approval",
                approval_type="preparation",
            )
            session.add(wo)
            await session.flush()

            jlog = await create_journey_for_work_order(wo_id, priority, session)
            wo.journey_log_id = jlog.jlog_id
            await record_status_change(wo_id, None, "pending_approval", session)

            await session.commit()

        log.info(
            "ppm_scheduler.work_order_created",
            work_order_id=wo_id,
            schedule_id=schedule.get("schedule_id"),
            asset_id=schedule.get("asset_id"),
            priority=priority,
        )
        await self.send_ppm_approval_notification(
            {"work_order_id": wo_id, "priority": priority},
            schedule,
        )
        return {"work_order_id": wo_id, "schedule_id": schedule.get("schedule_id")}

    async def send_ppm_approval_notification(
        self, work_order: Dict, schedule: Dict
    ) -> None:
        approver_email = schedule.get("approver_email") or "facilities.manager@aimms.local"
        approver_name = schedule.get("approver_name") or "Facilities Manager"
        if not settings.outlook_access_token:
            log.warning(
                "ppm_scheduler.approval_notification.skipped",
                reason="outlook_not_configured",
                work_order_id=work_order.get("work_order_id"),
            )
            return

        connector = OutlookConnector(settings.outlook_access_token, settings.outlook_user_email)
        notifier = NotificationService(connector=connector)
        await notifier.send_approval_request(
            work_order_id=work_order.get("work_order_id"),
            asset=schedule.get("asset_id") or "N/A",
            location=schedule.get("location") or "N/A",
            issue_description=schedule.get("task_description") or "PPM maintenance",
            priority=work_order.get("priority") or "medium",
            approver_email=approver_email,
            approver_name=approver_name,
        )
        log.info("ppm_scheduler.approval_notification.sent", work_order_id=work_order.get("work_order_id"), approver_email=approver_email)
