"""Step 7: Asset intelligence lookup — warranty, history, known issues."""
from typing import Dict, Any

from ..core.logging import get_logger

log = get_logger(__name__)


class AssetIntelligence:
    def __init__(self, aimms_api_url: str, cmms_api_url: str):
        self.aimms_api_url = aimms_api_url
        self.cmms_api_url = cmms_api_url

    async def lookup(self, asset_id: str) -> Dict[str, Any]:
        # Placeholder deterministic intelligence until live CMMS/BMS joins are enabled.
        asset_norm = (asset_id or "").lower()
        known_issues = []
        if "hvac" in asset_norm or "chiller" in asset_norm:
            known_issues.append({"issue": "Compressor overload trip", "occurrences": 3, "avg_cost": 2200})
        if "pump" in asset_norm:
            known_issues.append({"issue": "Seal leakage", "occurrences": 2, "avg_cost": 900})

        result = {
            "asset_id": asset_id,
            "warranty_status": "unknown",
            "mtbf_days": 180 if "hvac" in asset_norm else 240,
            "average_repair_cost": 1800 if known_issues else 850,
            "known_issues": known_issues,
        }
        log.info("asset_intelligence.lookup.complete", asset_id=asset_id, known_issues=len(known_issues))
        return result
