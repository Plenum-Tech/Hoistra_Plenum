"""Vendor scoring tool — wraps existing VendorScorer with a default vendor pool."""
from typing import Any, Dict, List, Optional

from ...intelligence.vendor_scorer import VendorScorer
from ...core.logging import get_logger

log = get_logger(__name__)

# Default vendor pool. In production this will be replaced with a DB query
# against plenum_cafm.vendors when that table is populated.
_VENDOR_POOL = [
    {
        "vendor_id": "V001",
        "name": "TechServ MEP Contractors",
        "expertise": ["HVAC", "Chiller", "AHU", "Cooling Tower", "MEP", "Mechanical"],
        "rating": 4.5,
        "available": True,
        "avg_response_hours": 4,
        "typical_rate": 350,
    },
    {
        "vendor_id": "V002",
        "name": "AlBaraka Facilities Services",
        "expertise": ["Electrical", "Generator", "Switchgear", "UPS", "LV Panel"],
        "rating": 4.2,
        "available": True,
        "avg_response_hours": 6,
        "typical_rate": 280,
    },
    {
        "vendor_id": "V003",
        "name": "Gulf Elevators LLC",
        "expertise": ["Elevator", "Escalator", "Lift", "Vertical Transport"],
        "rating": 4.7,
        "available": True,
        "avg_response_hours": 3,
        "typical_rate": 420,
    },
    {
        "vendor_id": "V004",
        "name": "Protec Fire Safety Systems",
        "expertise": ["Fire Alarm", "Sprinkler", "Fire Suppression", "Emergency", "Fire Safety"],
        "rating": 4.6,
        "available": True,
        "avg_response_hours": 2,
        "typical_rate": 500,
    },
    {
        "vendor_id": "V005",
        "name": "Consolidated FM Services",
        "expertise": ["Plumbing", "Civil", "Carpentry", "Painting", "General", "Joinery"],
        "rating": 3.9,
        "available": True,
        "avg_response_hours": 12,
        "typical_rate": 180,
    },
    {
        "vendor_id": "V006",
        "name": "PrimeTech BMS Solutions",
        "expertise": ["BMS", "SCADA", "Controls", "Automation", "Building Management"],
        "rating": 4.3,
        "available": True,
        "avg_response_hours": 8,
        "typical_rate": 450,
    },
]


class VendorTools:
    def __init__(self) -> None:
        self._scorer = VendorScorer()

    async def score_vendors(
        self,
        asset_type: str,
        priority: str,
        estimated_budget: float = 0,
        required_skills: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        required_skills = required_skills or []
        asset_lower = asset_type.lower()

        # Filter to relevant vendors by expertise keyword match
        relevant = [
            v for v in _VENDOR_POOL
            if any(
                asset_lower in e.lower() or e.lower() in asset_lower
                for e in v["expertise"]
            )
        ]
        # Always include "General" vendor as a fallback
        general = [v for v in _VENDOR_POOL if "General" in v["expertise"]]
        if not relevant:
            relevant = _VENDOR_POOL[:2]
        elif general and general[0] not in relevant:
            relevant.append(general[0])

        wo_context = {
            "priority": priority,
            "estimated_budget": estimated_budget,
            "required_skills": required_skills,
        }
        ranked = await self._scorer.rank_vendors(relevant, wo_context)

        log.info(
            "tool.score_vendors.done",
            asset_type=asset_type,
            candidates=len(ranked),
            top_vendor=ranked[0]["name"] if ranked else "none",
        )

        return {
            "recommended_vendor": ranked[0]["name"] if ranked else None,
            "vendor_id": ranked[0].get("vendor_id") if ranked else None,
            "top_score": ranked[0].get("score") if ranked else None,
            "ranking": [
                {
                    "name": v["name"],
                    "score": v["score"],
                    "rating": v.get("rating"),
                    "avg_response_hours": v.get("avg_response_hours"),
                    "expertise": v.get("expertise", [])[:3],
                }
                for v in ranked[:5]
            ],
        }
