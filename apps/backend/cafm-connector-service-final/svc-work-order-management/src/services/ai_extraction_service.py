"""
BE1-06: AI Extraction Service
Single OpenAI call that returns all 13 assessment blocks for a work order.
Covers: Criticality · Safety · Compliance · Location · Asset Intelligence ·
        Site Clearance · Parts List · Inventory · Vendors · Technician ·
        Schedule · Workspace Pin · Journey
"""
import json
import time
from openai import OpenAI
from typing import Dict, Any

from ..core.logging import get_logger

log = get_logger(__name__)

_SYSTEM = (
    "You are an expert AI facilities management assistant. "
    "You always respond with valid JSON only — no markdown, no explanation."
)

_USER_TEMPLATE = """Analyze this maintenance request and return a single JSON object with all assessment sections.

Work Order Context:
  Asset:        {asset}
  Location:     {location}
  Issue:        {issue_description}
  Priority:     {priority}
  Request Type: {request_type}
  Source:       {source}

Database Context (grounding data from CMMS; use this to improve decision quality):
{db_context}

Return ONLY this JSON structure — fill every field based on the asset type and issue:

{{
  "criticality": {{
    "level": "critical|high|medium|low",
    "safety_score": 0,
    "operational_score": 0,
    "financial_score": 0,
    "compliance_score": 0,
    "overall_score": 0,
    "response_time_hours": 4,
    "reasoning": "one sentence"
  }},
  "safety": {{
    "critical_safety_detected": false,
    "safety_types": [],
    "ppe_required": [],
    "confined_space": false,
    "hazmat": false,
    "fall_risk": false,
    "electrical_risk": false
  }},
  "compliance": {{
    "compliance_required": false,
    "types": [],
    "regulatory_body": null,
    "documentation_needed": []
  }},
  "location": {{
    "validated": true,
    "building": null,
    "floor": null,
    "room": null,
    "zone": null,
    "access_restrictions": []
  }},
  "asset_intelligence": {{
    "asset_type": "HVAC",
    "estimated_age_years": null,
    "warranty_status": "unknown",
    "last_maintenance_date": null,
    "known_issues": [],
    "recommendation": "repair|replace|inspect",
    "estimated_cost_range": "500-2000 AED"
  }},
  "site_clearance": {{
    "required": false,
    "hot_work_permit": false,
    "confined_space_permit": false,
    "electrical_isolation": false,
    "notes": null
  }},
  "parts_list": [
    {{"part_name": "example part", "quantity": 1, "urgency": "medium", "part_number": null}}
  ],
  "inventory": {{
    "check_required": true,
    "notes": "inventory check pending"
  }},
  "vendors": [
    {{"rank": 1, "vendor_type": "specialist", "required_certifications": [], "notes": "preferred"}}
  ],
  "technician": {{
    "required_skills": [],
    "certifications_needed": [],
    "team_size": 1,
    "seniority_level": "senior",
    "estimated_duration_hours": 2
  }},
  "schedule": {{
    "suggested_timeframe": "same_day",
    "estimated_duration_hours": 2,
    "time_constraints": [],
    "preferred_time_window": null,
    "constraints": []
  }},
  "workspace_pin": {{
    "pin_priority": "high",
    "quick_actions": [
      {{"label": "Assign Technician", "action": "assign_technician"}},
      {{"label": "Contact Vendor",    "action": "contact_vendor"}},
      {{"label": "Order Parts",       "action": "order_parts"}},
      {{"label": "Notify Requester",  "action": "notify_requester"}}
    ],
    "dashboard_flags": []
  }},
  "journey": {{
    "initial_step": "pending_approval",
    "next_steps": ["assign_technician", "schedule_visit"],
    "sla_deadline_hours": 4,
    "tracking_tags": []
  }}
}}"""


class AIExtractionService:
    """
    Calls OpenAI once and returns all 13 assessment blocks.
    Downstream services (email flow, intelligent WO engine) consume this output.
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract_all(self, work_order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Runs the comprehensive assessment for a work order context dict.
        Returns all 13 blocks as a nested dict.
        Sync — wrap with asyncio.to_thread() when calling from async code.
        """
        asset    = work_order_data.get("asset") or "Unknown asset"
        location = work_order_data.get("location") or "Unknown location"
        log.info(
            "ai_extraction.start",
            model=self.model,
            asset=asset,
            location=location,
            priority=work_order_data.get("priority", "medium"),
            has_db_context=bool(work_order_data.get("db_context")),
        )

        user_msg = _USER_TEMPLATE.format(
            asset=asset,
            location=location,
            issue_description=work_order_data.get("issue_description") or "",
            priority=work_order_data.get("priority", "medium"),
            request_type=work_order_data.get("request_type", "repair"),
            source=work_order_data.get("source", "manual"),
            db_context=json.dumps(work_order_data.get("db_context", {}), ensure_ascii=True),
        )

        t0 = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model,
            response_format={"type": "json_object"},
            max_tokens=2048,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": user_msg},
            ],
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000)

        result = json.loads(response.choices[0].message.content)
        log.info(
            "ai_extraction.complete",
            model=self.model,
            elapsed_ms=elapsed_ms,
            tokens_in=response.usage.prompt_tokens if response.usage else None,
            tokens_out=response.usage.completion_tokens if response.usage else None,
            criticality=result.get("criticality", {}).get("level"),
            safety_critical=result.get("safety", {}).get("critical_safety_detected"),
            compliance_required=result.get("compliance", {}).get("compliance_required"),
        )
        return result


# ---------------------------------------------------------------------------
# Quick smoke-test: python -m src.services.ai_extraction_service
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv

    load_dotenv()

    svc = AIExtractionService(
        api_key=os.environ["OPENAI_API_KEY"],
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    )
    result = svc.extract_all(
        {
            "asset": "HVAC Unit - Meeting Room 4B",
            "location": "Tower B, Floor 2, Room 4B",
            "issue_description": "HVAC has stopped working, loud rattling noise before shutdown",
            "priority": "urgent",
            "request_type": "repair",
            "source": "email",
        }
    )
    print(json.dumps(result, indent=2))
