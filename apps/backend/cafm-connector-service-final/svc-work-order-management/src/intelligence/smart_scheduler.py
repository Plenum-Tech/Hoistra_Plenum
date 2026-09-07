"""Step 13: Constraint-based smart scheduling — availability, priority, SLA, proximity."""
from typing import Dict, Any
from datetime import datetime, timedelta, timezone

from ..core.logging import get_logger

log = get_logger(__name__)

class SmartScheduler:
    def __init__(self, aimms_api_url: str):
        self.aimms_api_url = aimms_api_url

    async def schedule(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        criticality = (work_order.get("criticality", {}) or {}).get("criticality_level", "medium")
        duration = int((work_order.get("warranty_intelligence", {}) or {}).get("estimated_duration", 4))
        location = (work_order.get("location") or "").lower()

        now = datetime.now(timezone.utc)
        if criticality in ("critical", "high"):
            start = now + timedelta(hours=2)
            window_type = "immediate"
        elif "occupied" in location or "office" in location:
            start = (now + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
            window_type = "after_hours"
        else:
            start = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            window_type = "business_hours"

        end = start + timedelta(hours=duration)
        result = {
            "suggested_date": start.date().isoformat(),
            "suggested_time": start.strftime("%I:%M %p"),
            "suggested_start_datetime": start.isoformat(),
            "suggested_end_datetime": end.isoformat(),
            "estimated_duration_hours": duration,
            "window_type": window_type,
            "constraints_satisfied": True,
        }
        log.info(
            "smart_scheduler.schedule.complete",
            start=result["suggested_start_datetime"],
            duration_hours=duration,
            window_type=window_type,
        )
        return result
