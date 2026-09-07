"""DB-backed asset, location, and PPM lookup tools."""
from typing import Any, Dict
import uuid as _uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.asset import Asset
from ...models.location import Location
from ...models.ppm_schedule import PPMSchedule
from ...intelligence.asset_intelligence import AssetIntelligence
from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

_asset_intel = AssetIntelligence(settings.aimms_api_url, settings.cmms_api_url)


class AssetTools:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_assets(self, query: str) -> Dict[str, Any]:
        result = await self.session.execute(
            select(Asset)
            .where(or_(Asset.asset_name.ilike(f"%{query}%"), Asset.asset_code.ilike(f"%{query}%")))
            .order_by(Asset.asset_name)
            .limit(10)
        )
        assets = result.scalars().all()
        if not assets:
            return {
                "found": False,
                "count": 0,
                "results": [],
                "message": f"No assets found matching '{query}'. Try a different keyword or asset code.",
            }
        return {
            "found": True,
            "count": len(assets),
            "results": [
                {
                    "asset_id": str(a.asset_id),
                    "asset_code": a.asset_code or "",
                    "asset_name": a.asset_name,
                    "manufacturer": a.manufacturer or "",
                    "model": a.model or "",
                    "serial_number": a.serial_number or "",
                    "status": a.status or "active",
                }
                for a in assets
            ],
        }

    async def get_asset_details(self, asset_id: str) -> Dict[str, Any]:
        # Accept UUID or asset_code
        try:
            uid = _uuid.UUID(asset_id)
            result = await self.session.execute(select(Asset).where(Asset.asset_id == uid))
        except ValueError:
            result = await self.session.execute(
                select(Asset).where(Asset.asset_code == asset_id)
            )
        asset = result.scalar_one_or_none()
        if not asset:
            return {"found": False, "message": f"Asset '{asset_id}' not found."}

        # PPM schedules for this asset
        ppm_res = await self.session.execute(
            select(PPMSchedule).where(PPMSchedule.asset_id == asset.asset_id).limit(5)
        )
        schedules = ppm_res.scalars().all()

        # Asset intelligence (known failures, MTBF, cost)
        intel = await _asset_intel.lookup(asset.asset_code or str(asset.asset_id))

        return {
            "found": True,
            "asset_id": str(asset.asset_id),
            "asset_code": asset.asset_code or "",
            "asset_name": asset.asset_name,
            "manufacturer": asset.manufacturer or "",
            "model": asset.model or "",
            "serial_number": asset.serial_number or "",
            "status": asset.status or "active",
            "active": asset.active,
            "ppm_schedules": [
                {
                    "schedule_id": str(s.schedule_id),
                    "description": s.description or "",
                    "maintenance_type": s.maintenance_type or "",
                    "frequency": s.frequency or "",
                    "next_due_date": str(s.next_due_date) if s.next_due_date else None,
                    "status": s.status or "active",
                }
                for s in schedules
            ],
            "intelligence": intel,
        }

    async def search_locations(self, query: str) -> Dict[str, Any]:
        result = await self.session.execute(
            select(Location)
            .where(Location.name.ilike(f"%{query}%"))
            .order_by(Location.name)
            .limit(10)
        )
        locations = result.scalars().all()
        if not locations:
            return {
                "found": False,
                "count": 0,
                "results": [],
                "message": f"No locations found matching '{query}'.",
            }
        return {
            "found": True,
            "count": len(locations),
            "results": [
                {"location_id": str(l.location_id), "name": l.name, "type": l.type or ""}
                for l in locations
            ],
        }

    async def find_ppm_schedules(self, asset_id: str) -> Dict[str, Any]:
        try:
            uid = _uuid.UUID(asset_id)
        except ValueError:
            return {"found": False, "message": "Invalid asset_id format."}

        result = await self.session.execute(
            select(PPMSchedule)
            .where(PPMSchedule.asset_id == uid, PPMSchedule.status == "active")
            .limit(5)
        )
        schedules = result.scalars().all()
        if not schedules:
            return {"found": False, "count": 0, "schedules": [], "message": "No active PPM schedules found."}

        return {
            "found": True,
            "count": len(schedules),
            "schedules": [
                {
                    "schedule_id": str(s.schedule_id),
                    "description": s.description or "",
                    "maintenance_type": s.maintenance_type or "",
                    "frequency": s.frequency or "",
                    "next_due_date": str(s.next_due_date) if s.next_due_date else None,
                }
                for s in schedules
            ],
        }
