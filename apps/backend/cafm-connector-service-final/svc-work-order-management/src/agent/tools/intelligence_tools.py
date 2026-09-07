"""Intelligence tool implementations — thin async wrappers around existing intelligence modules."""
from typing import Any, Dict, List, Optional

from openai import OpenAI

from ...intelligence.criticality_assessor import CriticalityAssessor
from ...intelligence.safety_identifier import SafetyIdentifier
from ...intelligence.compliance_detector import ComplianceDetector
from ...intelligence.smart_scheduler import SmartScheduler
from ...intelligence.resource_allocator import ResourceAllocator
from ...intelligence.asset_intelligence import AssetIntelligence
from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)


class IntelligenceTools:
    """Wraps each existing intelligence module as an async tool callable by the GPT agent."""

    def __init__(self) -> None:
        _client = OpenAI(api_key=settings.openai_api_key)
        self._criticality = CriticalityAssessor(_client, model=settings.openai_model)
        self._safety = SafetyIdentifier()
        self._compliance = ComplianceDetector()
        self._scheduler = SmartScheduler(settings.aimms_api_url)
        self._allocator = ResourceAllocator(settings.aimms_api_url)
        self._asset_intel = AssetIntelligence(settings.aimms_api_url, settings.cmms_api_url)

    async def assess_criticality(
        self,
        asset: str,
        location: str,
        issue_description: str,
        priority: str = "medium",
        source: str = "chat",
    ) -> Dict[str, Any]:
        wo_context = {
            "asset": asset,
            "location": location,
            "issue_description": issue_description,
            "priority": priority,
            "source": source,
        }
        result = await self._criticality.assess(wo_context)
        log.info(
            "tool.assess_criticality.done",
            level=result.get("criticality_level"),
            score=result.get("overall_score"),
        )
        return result

    async def identify_safety_conditions(
        self,
        asset: str,
        location: str,
        issue_description: str,
    ) -> Dict[str, Any]:
        wo_context = {
            "asset": asset,
            "location": location,
            "issue_description": issue_description,
        }
        result = await self._safety.identify(wo_context)
        log.info(
            "tool.safety.done",
            critical=result.get("critical_safety_detected"),
            conditions=result.get("safety_conditions"),
        )
        return result

    async def detect_compliance_requirements(
        self,
        asset: str,
        issue_description: str,
        location: str = "",
    ) -> Dict[str, Any]:
        wo_context = {
            "asset": asset,
            "location": location,
            "issue_description": issue_description,
        }
        result = await self._compliance.detect(wo_context)
        log.info(
            "tool.compliance.done",
            required=result.get("compliance_required"),
            types=result.get("types"),
        )
        return result

    async def get_asset_intelligence(self, asset_id: str) -> Dict[str, Any]:
        result = await self._asset_intel.lookup(asset_id)
        log.info("tool.asset_intel.done", asset_id=asset_id, known_issues=len(result.get("known_issues", [])))
        return result

    async def get_scheduling_recommendation(
        self,
        criticality_level: str,
        estimated_duration_hours: float,
        location: str,
    ) -> Dict[str, Any]:
        # SmartScheduler reads 'criticality.criticality_level' and 'warranty_intelligence.estimated_duration'
        wo_context = {
            "criticality": {"criticality_level": criticality_level},
            "warranty_intelligence": {"estimated_duration": estimated_duration_hours},
            "location": location,
        }
        result = await self._scheduler.schedule(wo_context)
        log.info(
            "tool.schedule.done",
            date=result.get("suggested_date"),
            window=result.get("window_type"),
        )
        return result

    async def allocate_resources(self, issue_description: str) -> Dict[str, Any]:
        wo_context = {"issue_description": issue_description}
        result = await self._allocator.allocate(wo_context)
        log.info(
            "tool.allocate.done",
            technician=result.get("technician_name"),
            score=result.get("score"),
        )
        return result
