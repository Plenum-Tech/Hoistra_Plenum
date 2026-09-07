"""
BE1-11: CMMS Integration Service
Mock implementation — logs the work order payload and returns success.
Real Maximo / SAP PM HTTP calls slot in here later without changing the interface.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)


class CMMSIntegrationService:
    def __init__(self, cmms_api_url: str = "", cmms_api_key: str = ""):
        self.cmms_api_url = cmms_api_url
        self.cmms_api_key = cmms_api_key
        self._mock = not bool(cmms_api_url)

    async def send_work_order(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send a work order to the CMMS.
        Mock mode: logs the payload and returns a synthetic success response.
        Real mode: POST to self.cmms_api_url (implement when CMMS creds are available).
        """
        payload = self._build_cmms_payload(work_order)

        if self._mock:
            mock_cmms_id = f"CMMS-{work_order.get('work_order_id', 'UNKNOWN')}"
            logger.info(
                "CMMS mock: work order accepted",
                extra={
                    "cmms_wo_id": mock_cmms_id,
                    "aimms_wo_id": work_order.get("work_order_id"),
                    "asset": payload.get("assetnum"),
                    "priority": payload.get("priority"),
                    "payload": payload,
                },
            )
            return {
                "success": True,
                "mock": True,
                "cmms_wo_id": mock_cmms_id,
                "cmms_status": "APPROVED",
                "sent_at": datetime.now(timezone.utc).isoformat(),
                "payload_sent": payload,
            }

        # --- Real CMMS call (wire up when credentials are available) ---
        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.cmms_api_url}/api/workorders",
                json=payload,
                headers={"Authorization": f"Bearer {self.cmms_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "CMMS: work order created",
                extra={"cmms_wo_id": data.get("wonum"), "aimms_wo_id": work_order.get("work_order_id")},
            )
            return {
                "success": True,
                "mock": False,
                "cmms_wo_id": data.get("wonum"),
                "cmms_status": data.get("status"),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }

    async def sync_status_from_cmms(self, cmms_wo_id: str) -> Dict[str, Any]:
        """
        Pull current status for a CMMS work order back into AIMMS.
        Mock mode: returns a synthetic in-progress status.
        """
        if self._mock:
            logger.info("CMMS mock: status sync", extra={"cmms_wo_id": cmms_wo_id})
            return {
                "cmms_wo_id": cmms_wo_id,
                "cmms_status": "INPRG",
                "aimms_status": "active",
                "mock": True,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }

        import httpx  # noqa: PLC0415

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.cmms_api_url}/api/workorders/{cmms_wo_id}",
                headers={"Authorization": f"Bearer {self.cmms_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "cmms_wo_id": cmms_wo_id,
                "cmms_status": data.get("status"),
                "aimms_status": self._map_cmms_status(data.get("status", "")),
                "mock": False,
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_cmms_payload(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "wonum":       work_order.get("work_order_id"),
            "description": work_order.get("issue_description") or work_order.get("task_description"),
            "assetnum":    work_order.get("asset"),
            "location":    work_order.get("location"),
            "priority":    self.convert_priority_to_cmms(work_order.get("priority", "medium")),
            "worktype":    self.convert_type_to_cmms(work_order.get("request_type", "repair")),
            "schedstart":  work_order.get("scheduled_date"),
            "vendor":      work_order.get("vendor"),
            "estdur":      work_order.get("estimated_duration", 0),
            "status":      "APPROVED",
        }

    def convert_priority_to_cmms(self, priority: str) -> int:
        return {"low": 4, "medium": 3, "high": 2, "urgent": 1, "critical": 1}.get(priority, 3)

    def convert_type_to_cmms(self, request_type: str) -> str:
        return {"repair": "CM", "maintenance": "PM", "inspection": "INSP", "installation": "INST"}.get(
            request_type, "CM"
        )

    def _map_cmms_status(self, cmms_status: str) -> str:
        return {
            "WAPPR": "pending_approval",
            "APPR":  "preparing",
            "INPRG": "active",
            "COMP":  "completed",
            "CLOSE": "closed",
            "CAN":   "closed",
        }.get(cmms_status.upper(), "active")
