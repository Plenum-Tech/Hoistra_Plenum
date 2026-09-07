"""AIMMS workspace notification and pinning connector."""
from typing import Dict, Any
from datetime import datetime, timezone

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


class WorkspaceConnector:
    def __init__(self, aimms_api_url: str, aimms_api_key: str):
        self.aimms_api_url = (aimms_api_url or "").rstrip("/")
        self.aimms_api_key = aimms_api_key

    def _enabled(self) -> bool:
        return bool(self.aimms_api_url and self.aimms_api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-Key": self.aimms_api_key,
            "Content-Type": "application/json",
        }

    async def send_notification(self, notification: Dict[str, Any]) -> None:
        if not self._enabled():
            log.warning("workspace.notification.mock", notification=notification)
            return

        url = f"{self.aimms_api_url}/api/workspace/notifications"
        log.info("workspace.notification.start", url=url)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=notification, headers=self._headers())
            resp.raise_for_status()
        log.info("workspace.notification.complete")

    async def pin_work_order(self, work_order: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled():
            pin_id = f"PIN-{work_order.get('work_order_id', 'UNKNOWN')}"
            log.warning("workspace.pin.mock", pin_id=pin_id)
            return {"pin_id": pin_id, "pinned_at": datetime.now(timezone.utc).isoformat(), "mock": True}

        url = f"{self.aimms_api_url}/api/workspace/pins"
        payload = {
            "work_order_id": work_order.get("work_order_id"),
            "priority": work_order.get("priority"),
            "status": work_order.get("status"),
            "asset": work_order.get("asset"),
            "location": work_order.get("location"),
            "quick_actions": (
                work_order.get("workspace_pin", {}) or {}
            ).get("quick_actions", []),
        }
        log.info("workspace.pin.start", work_order_id=payload["work_order_id"])
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        log.info("workspace.pin.complete", pin_id=data.get("pin_id"))
        return data

    async def activate_safety_response_timer(self, work_order_id: str) -> None:
        if not self._enabled():
            log.warning("workspace.safety_timer.mock", work_order_id=work_order_id)
            return

        url = f"{self.aimms_api_url}/api/workspace/safety-timers"
        payload = {"work_order_id": work_order_id, "activated_at": datetime.now(timezone.utc).isoformat()}
        log.info("workspace.safety_timer.start", work_order_id=work_order_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
        log.info("workspace.safety_timer.complete", work_order_id=work_order_id)
