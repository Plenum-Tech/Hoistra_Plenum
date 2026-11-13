"""DB-backed asset, location, and PPM lookup tools."""
from typing import Any, Dict
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.asset import Asset
from ...models.location import Location
from ...models.ppm_schedule import PPMSchedule
from ...intelligence.asset_intelligence import AssetIntelligence
from ...shared import record_lookup
from ...config import settings
from ...core.logging import get_logger

log = get_logger(__name__)

_asset_intel = AssetIntelligence(settings.aimms_api_url, settings.cmms_api_url)


def withheld(page: str) -> Dict[str, Any]:
    """What a tool answers a caller held to pages that do not include ``page``: the same reason
    the routes give, so the chat says plainly that it is outside their pages."""
    return {"found": False, "ok": False, "reason": "page_not_allowed", "page": page,
            "error": "That is not part of the pages you have access to."}


def holds(principal, page: str) -> bool:
    pages = getattr(principal, "pages", None)
    return pages is None or page in pages


class AssetTools:
    """The chat's lookups, made for the signed-in caller.

    They ran with no caller at all: any company's assets and locations by name. With a caller
    every read is held to that caller's company and buildings (services.principal.scope_select,
    the rule the /api/assets routes already follow) and, for a caller held to some pages, the
    asset register needs the assets page. ``principal=None`` is the old unscoped read, kept only
    for code that has no caller; every route passes one.
    """

    def __init__(self, session: AsyncSession, principal=None) -> None:
        self.session = session
        self.principal = principal

    def _scoped(self, stmt, column):
        if self.principal is None:
            return stmt
        from ...services.principal import scope_select
        return scope_select(stmt, self.principal, column)

    def _in_scope_assets(self):
        """The ids of the assets this caller may see, as a subquery."""
        return self._scoped(select(Asset.asset_id), Asset.building_id)

    async def search_assets(self, query: str) -> Dict[str, Any]:
        if not holds(self.principal, "assets"):
            return withheld("assets")
        # The shared ladder (exact, compact, phrase, every word) over name and code, and no
        # close-spelling rung: the asset picked here is the one a new work order is raised on.
        # Every asset at the best rung comes back — never a silent LIMIT cut.
        found = await record_lookup.lookup(
            self.session, Asset, (Asset.asset_name, Asset.asset_code), query,
            lambda a: (a.asset_name, a.asset_code, f"{a.asset_name or ''} {a.asset_code or ''}"),
            order_by=(Asset.asset_name, Asset.asset_id),
            scope=lambda q: self._scoped(q, Asset.building_id),
        )
        assets = found["matches"]
        if not assets:
            out = {
                "found": False,
                "count": 0,
                "results": [],
                "message": f"No assets found matching '{query}'. Try a different keyword or asset code.",
            }
            if found["truncated"]:
                out["message"] += " " + record_lookup.describe(found, query, "asset", 0)
            return out
        shown = assets[:record_lookup.SHOW_LIMIT]
        out = {
            "found": True,
            "count": len(shown),
            "total": len(assets),
            "match": found["match"],
            "ambiguous": len(assets) > 1,
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
                for a in shown
            ],
        }
        note = record_lookup.describe(found, query, "asset", len(shown))
        if note:
            out["message"] = note
        return out

    async def get_asset_details(self, asset_id: str) -> Dict[str, Any]:
        if not holds(self.principal, "assets"):
            return withheld("assets")
        # Accept UUID or asset_code
        try:
            uid = _uuid.UUID(asset_id)
            stmt = select(Asset).where(Asset.asset_id == uid)
        except ValueError:
            stmt = select(Asset).where(Asset.asset_code == asset_id)
        result = await self.session.execute(self._scoped(stmt, Asset.building_id))
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
        # Same ladder as search_assets, over plenum_cafm.locations only. Buildings are not read
        # here: this tool runs with no caller, so a buildings read would cross every company.
        found = await record_lookup.lookup(
            self.session, Location, (Location.name,), query, lambda l: (l.name,),
            order_by=(Location.name, Location.location_id),
            scope=lambda q: self._scoped(q, Location.building_id),
        )
        locations = found["matches"]
        if not locations:
            out = {
                "found": False,
                "count": 0,
                "results": [],
                "message": f"No locations found matching '{query}'.",
            }
            if found["truncated"]:
                out["message"] += " " + record_lookup.describe(found, query, "location", 0)
            return out
        shown = locations[:record_lookup.SHOW_LIMIT]
        out = {
            "found": True,
            "count": len(shown),
            "total": len(locations),
            "match": found["match"],
            "ambiguous": len(locations) > 1,
            "results": [
                {"location_id": str(l.location_id), "name": l.name, "type": l.type or ""}
                for l in shown
            ],
        }
        note = record_lookup.describe(found, query, "location", len(shown))
        if note:
            out["message"] = note
        return out

    async def find_ppm_schedules(self, asset_id: str) -> Dict[str, Any]:
        try:
            uid = _uuid.UUID(asset_id)
        except ValueError:
            return {"found": False, "message": "Invalid asset_id format."}

        stmt = select(PPMSchedule).where(PPMSchedule.asset_id == uid, PPMSchedule.status == "active")
        if self.principal is not None:
            stmt = stmt.where(PPMSchedule.asset_id.in_(self._in_scope_assets()))
        result = await self.session.execute(stmt.limit(5))
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
