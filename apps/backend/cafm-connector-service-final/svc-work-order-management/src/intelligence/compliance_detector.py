"""Step 5: Regulatory compliance detection and tracking activation."""
from typing import Dict, Any

from ..core.logging import get_logger

log = get_logger(__name__)


class ComplianceDetector:
    async def detect(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        issue = (work_order.get("issue_description") or "").lower()
        asset = (work_order.get("asset") or "").lower()
        location = (work_order.get("location") or "").lower()

        comp_types: list[str] = []
        details: Dict[str, Any] = {}
        if any(k in issue for k in ["refrigerant", "chiller", "hvac"]) or "hvac" in asset:
            comp_types.append("environmental")
            details["environmental"] = {
                "regulations": ["EPA Clean Air Act", "Refrigerant Management"],
                "tracking_required": True,
            }
        if any(k in issue for k in ["boiler", "pressure", "steam"]):
            comp_types.append("safety_regulatory")
            details["safety_regulatory"] = {
                "regulations": ["ASME Boiler Code", "Local Pressure Vessel Rules"],
                "tracking_required": True,
            }
        if any(k in issue for k in ["energy", "efficiency"]) or "data center" in location:
            comp_types.append("energy")
            details["energy"] = {
                "regulations": ["ASHRAE 90.1"],
                "tracking_required": True,
            }

        result = {
            "compliance_required": bool(comp_types),
            "types": comp_types,
            "details": details,
        }
        log.info("compliance.detect.complete", required=result["compliance_required"], types=comp_types)
        return result
