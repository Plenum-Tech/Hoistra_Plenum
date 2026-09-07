"""Step 12: Smart resource allocation — skill matching, availability, workload balancing."""
from typing import Dict, Any

from ..core.logging import get_logger

log = get_logger(__name__)


class ResourceAllocator:
    def __init__(self, aimms_api_url: str):
        self.aimms_api_url = aimms_api_url

    async def allocate(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        issue = (work_order.get("issue_description") or "").lower()
        required_skills = []
        if "hvac" in issue or "cool" in issue:
            required_skills.append("HVAC Systems")
        if "electrical" in issue:
            required_skills.append("Electrical Systems")
        if "pump" in issue or "motor" in issue:
            required_skills.append("Mechanical Repair")

        tech_pool = [
            {"id": "TECH-001", "name": "Mike Johnson", "skills": ["HVAC Systems", "Mechanical Repair"], "current_week_hours": 30, "performance": 92},
            {"id": "TECH-002", "name": "Sara Khan", "skills": ["Electrical Systems", "Automation"], "current_week_hours": 22, "performance": 89},
            {"id": "TECH-003", "name": "Ravi Menon", "skills": ["Plumbing Systems", "Mechanical Repair"], "current_week_hours": 18, "performance": 84},
        ]

        def _score(t: Dict[str, Any]) -> float:
            skill_hits = sum(1 for s in required_skills if s in t["skills"])
            skill_score = (skill_hits / max(1, len(required_skills))) * 60
            workload_score = max(0, 100 - (t["current_week_hours"] * 2)) * 0.25
            perf_score = t["performance"] * 0.15
            return round(skill_score + workload_score + perf_score, 2)

        ranked = sorted(
            [{"technician_id": t["id"], "technician_name": t["name"], "score": _score(t), "skills": t["skills"]} for t in tech_pool],
            key=lambda x: x["score"],
            reverse=True,
        )
        best = ranked[0] if ranked else {"technician_id": None, "technician_name": "Manual Assignment", "score": 0, "skills": []}
        best["required_skills"] = required_skills
        log.info("resource_allocator.allocate.complete", technician_id=best.get("technician_id"), score=best.get("score"))
        return best
