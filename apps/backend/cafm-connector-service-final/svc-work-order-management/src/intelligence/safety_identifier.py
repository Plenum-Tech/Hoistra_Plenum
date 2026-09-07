"""Step 4: Safety condition identification and response-time activation."""
from typing import Dict, Any

from ..core.logging import get_logger

log = get_logger(__name__)


class SafetyIdentifier:
    async def identify(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        issue = (work_order.get("issue_description") or "").lower()
        location = (work_order.get("location") or "").lower()
        asset = (work_order.get("asset") or "").lower()

        conditions: list[str] = []
        permits: list[str] = []
        safety_types: list[str] = []

        if any(k in issue for k in ["roof", "height", "fall"]) or "roof" in location:
            conditions.append("fall_protection")
            permits.append("fall_protection_plan")
            safety_types.append("Fall Protection")
        if any(k in issue for k in ["confined", "tank", "duct"]):
            conditions.append("confined_space")
            permits.append("confined_space_permit")
            safety_types.append("Confined Space")
        if any(k in issue for k in ["electrical", "shock", "panel", "live wire"]) or "electrical" in asset:
            conditions.append("electrical_hazard")
            permits.append("lockout_tagout")
            safety_types.append("Electrical Safety")
        if any(k in issue for k in ["gas", "chemical", "refrigerant", "hazmat"]):
            conditions.append("hazardous_materials")
            permits.append("hazmat_handling_clearance")
            safety_types.append("Hazardous Materials")

        critical = bool({"electrical_hazard", "hazardous_materials"} & set(conditions))
        response_tracking = critical or len(conditions) > 1
        result = {
            "critical_safety_detected": critical,
            "safety_conditions": conditions,
            "safety_types": safety_types,
            "permits_required": permits,
            "response_time_tracking": response_tracking,
        }
        log.info(
            "safety.identify.complete",
            critical=critical,
            conditions_count=len(conditions),
            permits_count=len(permits),
        )
        return result
