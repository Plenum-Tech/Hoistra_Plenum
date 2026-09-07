"""Tool dispatcher — routes GPT tool_call names to their implementations."""
import json
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from .asset_tools import AssetTools
from .intelligence_tools import IntelligenceTools
from .vendor_tools import VendorTools
from .workflow_tools import WorkflowTools
from ...core.logging import get_logger

log = get_logger(__name__)

# Intelligence tools are stateless — one shared instance per process
_intel = IntelligenceTools()
_vendors = VendorTools()


class ToolExecutor:
    def __init__(self, session: AsyncSession, session_id: str) -> None:
        self._session = session
        self._assets = AssetTools(session)
        self._workflow = WorkflowTools(session, session_id)

    async def execute(self, name: str, arguments: Dict[str, Any]) -> Any:
        log.info("tool.dispatch", name=name, args=list(arguments.keys()))
        try:
            match name:
                # ── Asset / location lookups ───────────────────────────────
                case "search_assets":
                    return await self._assets.search_assets(**arguments)
                case "get_asset_details":
                    return await self._assets.get_asset_details(**arguments)
                case "search_locations":
                    return await self._assets.search_locations(**arguments)
                case "find_ppm_schedules":
                    return await self._assets.find_ppm_schedules(**arguments)

                # ── Intelligence tools ─────────────────────────────────────
                case "assess_criticality":
                    return await _intel.assess_criticality(**arguments)
                case "identify_safety_conditions":
                    return await _intel.identify_safety_conditions(**arguments)
                case "detect_compliance_requirements":
                    return await _intel.detect_compliance_requirements(**arguments)
                case "get_asset_intelligence":
                    return await _intel.get_asset_intelligence(**arguments)
                case "get_scheduling_recommendation":
                    return await _intel.get_scheduling_recommendation(**arguments)
                case "allocate_resources":
                    return await _intel.allocate_resources(**arguments)

                # ── Vendor scoring ─────────────────────────────────────────
                case "score_vendors":
                    return await _vendors.score_vendors(**arguments)

                # ── Workflow tools ─────────────────────────────────────────
                case "create_work_order":
                    return await self._workflow.create_work_order(**arguments)
                case "suggest_approval_chain":
                    return await self._workflow.suggest_approval_chain(**arguments)
                case "request_approval":
                    return await self._workflow.request_approval(**arguments)
                case "send_approval_request_email":
                    return await self._workflow.send_approval_request_email(**arguments)
                case "get_work_order_status_track":
                    return await self._workflow.get_work_order_status_track(**arguments)

                case _:
                    log.warning("tool.unknown", name=name)
                    return {"error": f"Unknown tool: {name}"}

        except TypeError as exc:
            log.error("tool.bad_arguments", name=name, error=str(exc))
            return {"error": f"Invalid arguments for {name}: {exc}"}
        except Exception as exc:
            await self._session.rollback()
            log.error("tool.execute.error", name=name, error=str(exc), exc_info=True)
            return {"error": f"Tool '{name}' failed: {exc}"}
