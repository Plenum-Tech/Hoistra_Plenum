"""Step 11: Composite vendor scoring — rating 35%, availability 30%, expertise 25%, response 5%, budget 5%."""
from typing import Dict, Any, List


class VendorScorer:
    def score(self, vendor: Dict[str, Any], work_order: Dict[str, Any]) -> float:
        rating_score = vendor.get("rating", 0) * 20
        availability_score = 100 if vendor.get("available") else 30
        expertise_score = self._expertise_match(
            vendor.get("expertise", []), work_order.get("required_skills", [])
        )
        response_score = 100 - min(vendor.get("avg_response_hours", 24) * 2, 100)
        budget_score = self._budget_fit(
            vendor.get("typical_rate", 0), work_order.get("estimated_budget", 0)
        )
        return round(
            rating_score * 0.35
            + availability_score * 0.30
            + expertise_score * 0.25
            + response_score * 0.05
            + budget_score * 0.05,
            2,
        )

    def _expertise_match(self, vendor_skills: List[str], required: List[str]) -> float:
        if not required:
            return 100.0
        matched = sum(1 for s in required if s in vendor_skills)
        return (matched / len(required)) * 100

    def _budget_fit(self, rate: float, budget: float) -> float:
        if budget <= 0:
            return 50.0
        ratio = rate / budget
        if ratio <= 0.8:
            return 100.0
        if ratio <= 1.0:
            return 80.0
        if ratio <= 1.2:
            return 50.0
        return 20.0

    async def rank_vendors(
        self, vendors: List[Dict], work_order: Dict
    ) -> List[Dict[str, Any]]:
        scored = [
            {**v, "score": self.score(v, work_order)} for v in vendors
        ]
        return sorted(scored, key=lambda x: x["score"], reverse=True)
