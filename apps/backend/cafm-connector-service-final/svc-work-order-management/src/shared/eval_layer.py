"""Evaluation gates for the work order pipeline (mirrors eval_layer pattern in other services)."""
from typing import Dict, Any


class WOEvalLayer:
    """Hard validation gates before a work order advances to the next step."""

    def validate_extracted_email_data(self, data: Dict[str, Any]) -> bool:
        required = ["asset", "location", "issue_description", "requester_name", "requester_email"]
        return all(data.get(f) for f in required)

    def validate_criticality_assessment(self, assessment: Dict[str, Any]) -> bool:
        required = ["level", "overall_score", "response_time_hours"]
        return all(assessment.get(f) is not None for f in required)

    def validate_work_order_before_cmms(self, work_order: Dict[str, Any]) -> bool:
        required = ["asset", "location", "scheduled_date", "vendor"]
        return all(work_order.get(f) for f in required)
