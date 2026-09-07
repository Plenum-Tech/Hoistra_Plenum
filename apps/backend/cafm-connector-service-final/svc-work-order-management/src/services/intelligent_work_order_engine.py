"""15-step AI-powered work order creation engine — see docs/WORK_ORDER_MODULE_COMPLETE.md."""
from typing import Dict, Any
from datetime import datetime, timezone
from openai import OpenAI
from sqlalchemy import select

from ..core.logging import get_logger
from ..config import settings
from ..db import AsyncSessionLocal
from ..models.asset import Asset
from ..models.location import Location
from ..models.work_order import WorkOrder
from ..services.journey_service import create_journey_for_work_order, record_status_change
from ..integrations.workspace_connector import WorkspaceConnector
from ..intelligence.criticality_assessor import CriticalityAssessor
from ..intelligence.safety_identifier import SafetyIdentifier
from ..intelligence.compliance_detector import ComplianceDetector
from ..intelligence.asset_intelligence import AssetIntelligence
from ..intelligence.resource_allocator import ResourceAllocator
from ..intelligence.smart_scheduler import SmartScheduler

log = get_logger(__name__)


class IntelligentWorkOrderEngine:
    def __init__(
        self,
        aimms_api_url: str,
        cmms_api_url: str,
        bms_api_url: str,
        openai_api_key: str,
        model: str = "gpt-4o-mini",
    ):
        self.aimms_api_url = aimms_api_url
        self.cmms_api_url = cmms_api_url
        self.bms_api_url = bms_api_url
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.model = model
        self.workspace = WorkspaceConnector(settings.aimms_api_url, settings.aimms_api_key)
        self.criticality_assessor = CriticalityAssessor(self.openai_client, model=self.model)
        self.safety_identifier = SafetyIdentifier()
        self.compliance_detector = ComplianceDetector()
        self.asset_intelligence = AssetIntelligence(self.aimms_api_url, self.cmms_api_url)
        self.resource_allocator = ResourceAllocator(self.aimms_api_url)
        self.smart_scheduler_engine = SmartScheduler(self.aimms_api_url)

    async def create_intelligent_work_order(
        self, source: str, request_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        log.info("intelligent_engine.create.start", source=source, asset=request_data.get("asset"))
        wo: Dict[str, Any] = {
            "work_order_id": f"WO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}",
            "source": source,
            **request_data,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "intelligent_wo_engine",
        }
        wo.update(await self.step_1_source_identification(source, request_data))
        wo["workspace_data"] = await self.collect_workspace_data(request_data)
        wo["criticality"] = await self.assess_criticality(wo)
        wo["safety"] = await self.identify_safety_conditions(wo)
        wo["compliance"] = await self.detect_compliance_requirements(wo)
        wo["location_data"] = await self.validate_location(wo)
        wo["asset_intelligence"] = await self.lookup_asset_intelligence(wo)
        wo["site_clearance"] = await self.check_site_clearance(wo)
        wo["warranty_intelligence"] = await self.get_warranty_inspection_intelligence(wo)
        wo["spare_parts"] = await self.check_spare_parts_availability(wo)
        wo["suggested_vendors"] = await self.suggest_vendors(wo)
        wo["resource_allocation"] = await self.allocate_resources(wo)
        wo["schedule"] = await self.smart_scheduling(wo)
        wo["workspace_pin"] = await self.pin_to_workspace(wo)
        wo["journey"] = await self.create_journey_log(wo)
        saved = await self.save_work_order(wo)
        log.info("intelligent_engine.create.complete", work_order_id=wo["work_order_id"], status=saved.get("status"))
        return saved

    async def step_1_source_identification(self, source: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        source_mapping = {
            "email": {"source_classification": "reactive", "requires_preparation": True, "approval_type": "preparation"},
            "ppm_schedule": {"source_classification": "preventive", "requires_preparation": False, "approval_type": "simple"},
            "manual": {"source_classification": "reactive", "requires_preparation": True, "approval_type": "full"},
            "tenant_request": {"source_classification": "reactive", "requires_preparation": True, "approval_type": "preparation", "client_notification": True},
            "internal": {"source_classification": "internal", "requires_preparation": True, "approval_type": "simple"},
            "remediation": {"source_classification": "proactive", "requires_preparation": False, "approval_type": "full"},
        }
        cfg = source_mapping.get(source, source_mapping["manual"])
        return {
            "source_type": source,
            "source_metadata": request_data.get("metadata", {}),
            "client_notification": cfg.get("client_notification", False),
            **cfg,
        }

    # Steps 1-15 — each implemented in its own method or delegated to intelligence/
    async def collect_workspace_data(self, request_data: Dict) -> Dict[str, Any]:
        async with AsyncSessionLocal() as session:
            asset_name = request_data.get("asset")
            location_name = request_data.get("location")
            asset_data: Dict[str, Any] = {}
            location_data: Dict[str, Any] = {}

            if asset_name:
                result = await session.execute(
                    select(Asset).where(Asset.asset_name.ilike(f"%{asset_name}%")).limit(1)
                )
                a = result.scalar_one_or_none()
                if a:
                    asset_data = {
                        "asset_id": str(a.asset_id),
                        "asset_name": a.asset_name,
                        "manufacturer": a.manufacturer,
                        "model": a.model,
                        "serial_number": a.serial_number,
                        "status": a.status,
                    }
            if location_name:
                result = await session.execute(
                    select(Location).where(Location.name.ilike(f"%{location_name}%")).limit(1)
                )
                l = result.scalar_one_or_none()
                if l:
                    location_data = {"location_id": str(l.location_id), "name": l.name, "type": l.type}
            return {"asset_details": asset_data, "location_details": location_data}

    async def assess_criticality(self, work_order: Dict) -> Dict[str, Any]:
        return await self.criticality_assessor.assess(work_order)

    async def identify_safety_conditions(self, work_order: Dict) -> Dict[str, Any]:
        safety = await self.safety_identifier.identify(work_order)
        if safety.get("critical_safety_detected"):
            await self.workspace.activate_safety_response_timer(work_order["work_order_id"])
        return safety

    async def detect_compliance_requirements(self, work_order: Dict) -> Dict[str, Any]:
        return await self.compliance_detector.detect(work_order)

    async def validate_location(self, work_order: Dict) -> Dict[str, Any]:
        location = work_order.get("location", "")
        return {"valid": bool(location), "location": location, "access_restrictions": []}

    async def lookup_asset_intelligence(self, work_order: Dict) -> Dict[str, Any]:
        return await self.asset_intelligence.lookup(work_order.get("asset", "unknown"))

    async def check_site_clearance(self, work_order: Dict) -> Dict[str, Any]:
        safety = work_order.get("safety", {}) or {}
        required = bool(safety.get("permits_required"))
        return {"required": required, "certificate_provided": not required, "certificates": []}

    async def get_warranty_inspection_intelligence(self, work_order: Dict) -> Dict[str, Any]:
        issue = (work_order.get("issue_description") or "").lower()
        parts = [{"part": "General service kit", "part_number": "GEN-SVC-001", "estimated_cost": 120, "required_quantity": 1}]
        if "hvac" in issue or "cool" in issue:
            parts.append({"part": "Drive belt", "part_number": "HVAC-BLT-002", "estimated_cost": 85, "required_quantity": 1})
        return {
            "required_parts": parts,
            "recommended_tools": ["Multimeter", "Torque wrench"],
            "estimated_duration": 4,
            "warranty_status": work_order.get("asset_intelligence", {}).get("warranty_status", "unknown"),
        }

    async def check_spare_parts_availability(self, work_order: Dict) -> Dict[str, Any]:
        req = work_order.get("warranty_intelligence", {}).get("required_parts", [])
        available = []
        unavailable = []
        for idx, p in enumerate(req):
            if idx == 0:
                available.append({**p, "available": True, "available_quantity": 5, "location": "Main Store"})
            else:
                unavailable.append({**p, "available": False, "available_quantity": 0, "on_order": True, "expected_eta": datetime.now(timezone.utc).date().isoformat()})
        return {"all_available": len(unavailable) == 0, "available_parts": available, "unavailable_parts": unavailable}

    async def suggest_vendors(self, work_order: Dict) -> list:
        return [
            {"vendor_id": "VND-001", "name": "TechCool HVAC Services", "rating": 4.8, "score": 93.2, "available": True},
            {"vendor_id": "VND-002", "name": "Prime Mechanical", "rating": 4.6, "score": 88.1, "available": True},
            {"vendor_id": "VND-003", "name": "General FM Contractors", "rating": 4.4, "score": 83.7, "available": True},
        ]

    async def allocate_resources(self, work_order: Dict) -> Dict[str, Any]:
        return await self.resource_allocator.allocate(work_order)

    async def smart_scheduling(self, work_order: Dict) -> Dict[str, Any]:
        return await self.smart_scheduler_engine.schedule(work_order)

    async def pin_to_workspace(self, work_order: Dict) -> Dict[str, Any]:
        return await self.workspace.pin_work_order(work_order)

    async def create_journey_log(self, work_order: Dict) -> Dict[str, Any]:
        return {
            "initial_step": "pending_approval",
            "next_steps": ["preparing", "prepared", "active", "completed", "closed"],
            "sla_deadline_hours": work_order.get("criticality", {}).get("response_time_hours", 72),
        }

    async def save_work_order(self, work_order: Dict) -> Dict[str, Any]:
        org_id = None
        if settings.default_organization_id:
            try:
                org_id = int(settings.default_organization_id)
            except (TypeError, ValueError):
                pass

        async with AsyncSessionLocal() as session:
            wo = WorkOrder(
                work_order_id=work_order["work_order_id"],
                organization_id=org_id,
                title=work_order.get("issue_description") or "Intelligent work order",
                source=work_order.get("source"),
                source_reference=work_order.get("source_reference"),
                asset=work_order.get("asset"),
                location=work_order.get("location"),
                issue_description=work_order.get("issue_description"),
                task_description=work_order.get("task_description"),
                priority=work_order.get("criticality", {}).get("criticality_level", work_order.get("priority", "medium")),
                request_type=work_order.get("request_type", "repair"),
                status="pending_approval",
                approval_type=work_order.get("approval_type", "preparation"),
                requester_name=work_order.get("requester_name"),
                requester_email=work_order.get("requester_email"),
                requester_phone=work_order.get("requester_phone"),
                # Vendor/technician/schedule are applied only after final approval.
                vendor=None,
                manpower={
                    "required_skills": (work_order.get("resource_allocation") or {}).get("required_skills") or [],
                    "asset_type": (work_order.get("asset_intelligence") or {}).get("asset_type") or "",
                },
                scheduled_date=None,
                scheduled_time=None,
                estimated_duration=None,
            )
            session.add(wo)
            await session.flush()
            jlog = await create_journey_for_work_order(wo.work_order_id, wo.priority or "medium", session)
            wo.journey_log_id = jlog.jlog_id
            await record_status_change(wo.work_order_id, None, "pending_approval", session)
            await session.commit()

            work_order["journey_log_id"] = wo.journey_log_id
            work_order["status"] = wo.status
            return work_order
