"""CMMS connector — abstracts Maximo / SAP PM differences."""
from typing import Dict, Any
from datetime import datetime, timezone

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


class CMMSConnector:
    def __init__(self, cmms_api_url: str, cmms_api_key: str):
        self.cmms_api_url = (cmms_api_url or "").rstrip("/")
        self.cmms_api_key = cmms_api_key

    def _enabled(self) -> bool:
        return bool(self.cmms_api_url and self.cmms_api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cmms_api_key}",
            "Content-Type": "application/json",
        }

    async def create_work_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled():
            fake_id = f"CMMS-MOCK-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            log.warning("cmms.create.mock_mode", cmms_work_order_id=fake_id)
            return {"cmms_work_order_id": fake_id, "status": "created", "mock": True}

        url = f"{self.cmms_api_url}/work-orders"
        log.info("cmms.create.start", url=url, source_id=payload.get("work_order_id"))
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        log.info("cmms.create.complete", cmms_work_order_id=data.get("cmms_work_order_id"))
        return data

    async def get_work_order(self, cmms_wo_id: str) -> Dict[str, Any]:
        if not self._enabled():
            log.warning("cmms.get.mock_mode", cmms_work_order_id=cmms_wo_id)
            return {"cmms_work_order_id": cmms_wo_id, "status": "unknown", "mock": True}

        url = f"{self.cmms_api_url}/work-orders/{cmms_wo_id}"
        log.debug("cmms.get.start", cmms_work_order_id=cmms_wo_id)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        log.debug("cmms.get.complete", cmms_work_order_id=cmms_wo_id)
        return data

    async def update_work_order(self, cmms_wo_id: str, updates: Dict[str, Any]) -> None:
        if not self._enabled():
            log.warning("cmms.update.mock_mode", cmms_work_order_id=cmms_wo_id, updates=updates)
            return

        url = f"{self.cmms_api_url}/work-orders/{cmms_wo_id}"
        log.info("cmms.update.start", cmms_work_order_id=cmms_wo_id, fields=list(updates.keys()))
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.patch(url, json=updates, headers=self._headers())
            resp.raise_for_status()
        log.info("cmms.update.complete", cmms_work_order_id=cmms_wo_id)
