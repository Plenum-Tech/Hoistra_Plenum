"""Step 3: AI-powered criticality assessment (safety / operational / financial / compliance)."""
from typing import Dict, Any
from datetime import datetime, timezone
from openai import OpenAI
import json

from ..core.logging import get_logger

log = get_logger(__name__)


class CriticalityAssessor:
    def __init__(self, openai_client: OpenAI, model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.model = model

    async def assess(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        issue = (work_order.get("issue_description") or "").lower()
        priority = (work_order.get("priority") or "medium").lower()

        prompt = (
            "Assess work-order criticality and return strict JSON with keys: "
            "safety_score, operational_score, financial_score, compliance_score, "
            "overall_score, criticality_level, response_time_hours, reasoning."
        )
        context = {
            "asset": work_order.get("asset"),
            "location": work_order.get("location"),
            "issue_description": work_order.get("issue_description"),
            "priority": priority,
            "source": work_order.get("source"),
        }
        try:
            response = self.openai_client.chat.completions.create(
                model=self.model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "Return valid JSON only."},
                    {"role": "user", "content": f"{prompt}\n{json.dumps(context)}"},
                ],
                max_tokens=600,
            )
            parsed = json.loads(response.choices[0].message.content)
            parsed["assessed_at"] = datetime.now(timezone.utc).isoformat()
            log.info(
                "criticality.ai.complete",
                criticality_level=parsed.get("criticality_level"),
                response_time_hours=parsed.get("response_time_hours"),
            )
            return parsed
        except Exception as exc:
            # Deterministic fallback keeps flow alive even when AI call fails.
            log.warning("criticality.ai.fallback", exc_info=exc)
            safety_score = 30
            operational_score = 40
            financial_score = 35
            compliance_score = 30

            if any(k in issue for k in ["fire", "shock", "gas leak", "hazard", "smoke"]):
                safety_score = 90
            if any(k in issue for k in ["down", "stopped", "failure", "outage"]):
                operational_score = 85
            if any(k in issue for k in ["chiller", "transformer", "elevator", "boiler"]):
                financial_score = max(financial_score, 75)
            if any(k in issue for k in ["epa", "permit", "compliance", "inspection"]):
                compliance_score = 80

            pri_boost = {"critical": 20, "urgent": 15, "high": 10, "medium": 0, "low": -5}.get(priority, 0)
            overall = min(
                100,
                int((safety_score * 0.35) + (operational_score * 0.3) + (financial_score * 0.2) + (compliance_score * 0.15) + pri_boost),
            )
            if overall >= 85:
                level = "critical"
                response = 4
            elif overall >= 70:
                level = "high"
                response = 24
            elif overall >= 50:
                level = "medium"
                response = 72
            else:
                level = "low"
                response = 120

            return {
                "safety_score": safety_score,
                "operational_score": operational_score,
                "financial_score": financial_score,
                "compliance_score": compliance_score,
                "overall_score": overall,
                "criticality_level": level,
                "response_time_hours": response,
                "reasoning": "Computed using deterministic fallback scoring from issue keywords and priority.",
                "assessed_at": datetime.now(timezone.utc).isoformat(),
            }
