"""Building Management System connector — asset and location data."""
from typing import Dict, Any

import httpx

from ..core.logging import get_logger

log = get_logger(__name__)


class BMSConnector:
    def __init__(self, bms_api_url: str):
        self.bms_api_url = (bms_api_url or "").rstrip("/")

    def _enabled(self) -> bool:
        return bool(self.bms_api_url)

    async def get_asset(self, asset_id: str) -> Dict[str, Any]:
        if not self._enabled():
            log.warning("bms.asset.mock", asset_id=asset_id)
            return {"asset_id": asset_id, "status": "unknown", "mock": True}

        url = f"{self.bms_api_url}/assets/{asset_id}"
        log.debug("bms.asset.start", asset_id=asset_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        log.debug("bms.asset.complete", asset_id=asset_id)
        return data

    async def get_location(self, location_id: str) -> Dict[str, Any]:
        if not self._enabled():
            log.warning("bms.location.mock", location_id=location_id)
            return {"location_id": location_id, "status": "unknown", "mock": True}

        url = f"{self.bms_api_url}/locations/{location_id}"
        log.debug("bms.location.start", location_id=location_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
        log.debug("bms.location.complete", location_id=location_id)
        return data
