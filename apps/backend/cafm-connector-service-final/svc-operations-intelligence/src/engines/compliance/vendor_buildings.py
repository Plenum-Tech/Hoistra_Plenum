"""Vendor ↔ building assignments (12 Oct 2026).

"Buildings served" was only ever inferred — from certificates, work orders and invoices that
name the vendor — so a vendor just hoisted, with none of those yet, served nowhere however many
buildings the admin said it works in. An assignment is that statement, stored
(migrations/vendor_buildings.sql): made from Hoist vendors' buildings question or the Vendors
page, admin only, own company only, ended rather than deleted, and recorded in the vendor trail.

A database where the migration has not run yet answers ``not_set_up`` instead of failing.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.approvals import PLATFORM_FEATURE, write_audit
from ...shared.vendor_identity import live_vendor_sql, vendor_link_refusal
from ..auth import vendor_audit

#: The ways an assignment is made, as the trail names them.
SOURCES = ("hoist", "manual")

NOT_SET_UP = {
    "ok": False, "reason": "not_set_up",
    "error": "Assigning vendors to buildings is not set up on this database yet.",
}

_READY = "SELECT to_regclass('plenum_cafm.vendor_buildings') IS NOT NULL"

_LIST = f"""
    SELECT vb.vendor_id, to_jsonb(v)->>'vendor_name' AS vendor_name, vb.building_id::text AS building_id,
           COALESCE(b.name, vb.building_name) AS building_name,
           vb.source, vb.assigned_at, vb.assigned_by
      FROM plenum_cafm.vendor_buildings vb
      JOIN plenum_cafm.vendors v ON lower(v.id::text) = lower(vb.vendor_id) AND {live_vendor_sql('v')}
      LEFT JOIN plenum_cafm.buildings b
        ON b.building_id = vb.building_id AND b.organization_id = vb.organization_id
     WHERE vb.organization_id = CAST(:org AS uuid) AND vb.removed_at IS NULL"""

_BUILDINGS = """
    SELECT b.building_id::text AS building_id, b.name
      FROM plenum_cafm.buildings b
     WHERE b.organization_id = CAST(:org AS uuid) AND b.building_id::text = ANY(:ids)"""

_INSERT = """
    INSERT INTO plenum_cafm.vendor_buildings
           (organization_id, vendor_id, building_id, building_name, source, assigned_by)
    VALUES (CAST(:org AS uuid), :vid, CAST(:bid AS uuid), :name, :source, :uid)
    ON CONFLICT (organization_id, vendor_id, building_id) WHERE removed_at IS NULL DO NOTHING"""

_NAME = """
    SELECT to_jsonb(v)->>'vendor_name' FROM plenum_cafm.vendors v
     WHERE lower(v.id::text) = lower(:vid) AND to_jsonb(v)->>'organization_id' = :org LIMIT 1"""

_END = """
    UPDATE plenum_cafm.vendor_buildings
       SET removed_at = now(), removed_by = :uid
     WHERE organization_id = CAST(:org AS uuid) AND lower(vendor_id) = lower(:vid)
       AND building_id = CAST(:bid AS uuid) AND removed_at IS NULL
    RETURNING building_name"""

#: The vendor's id as its own row spells it — what is stored, so the unique index, ON CONFLICT
#: and the delete's exact match all agree whatever case the caller sent (second review).
_CANON = """
    SELECT v.id::text FROM plenum_cafm.vendors v
     WHERE lower(v.id::text) = lower(:vid) AND to_jsonb(v)->>'organization_id' = :org LIMIT 1"""


async def _vendor_party(session: AsyncSession, vid: str, org: str) -> dict[str, Any]:
    """{id, name} for the trail entry; the name is what the vendor is called right now."""
    try:
        # In a savepoint: a failed read must not abort the transaction the write is in.
        async with session.begin_nested():
            name = (await session.execute(text(_NAME), {"vid": vid, "org": org})).scalar()
    except Exception:  # noqa: BLE001 — the trail names the id when the name cannot be read
        name = None
    return {"id": vid, "name": name}


async def ready(session: AsyncSession) -> bool:
    try:
        return bool((await session.execute(text(_READY))).scalar())
    except Exception:  # noqa: BLE001 — unreadable is treated as not there
        return False


def _canon_uuid(v: object) -> str | None:
    try:
        return str(UUID(str(v).strip()))
    except (TypeError, ValueError, AttributeError):
        return None


async def list_assignments(session: AsyncSession, *, organization_id: UUID | str,
                           vendor_id: str | None = None,
                           building_ids: tuple[UUID, ...] | None = None) -> dict[str, Any]:
    """The company's live assignments (a retired vendor's are left out), optionally one vendor's,
    narrowed to ``building_ids`` for a caller allocated to some buildings only."""
    if not await ready(session):
        return {"ok": True, "available": False, "assignments": []}
    sql, params = _LIST, {"org": str(organization_id)}
    if vendor_id:
        sql += " AND lower(vb.vendor_id) = lower(:vid)"
        params["vid"] = str(vendor_id)
    if building_ids is not None:
        sql += " AND vb.building_id::text = ANY(:scope)"
        params["scope"] = [str(b) for b in building_ids]
    sql += " ORDER BY building_name NULLS LAST, vb.building_id"
    rows = (await session.execute(text(sql), params)).mappings().all()
    return {"ok": True, "available": True, "assignments": [
        {"vendor_id": r["vendor_id"], "vendor_name": r["vendor_name"], "building_id": r["building_id"],
         "building_name": r["building_name"], "source": r["source"],
         "assigned_at": r["assigned_at"].isoformat() if hasattr(r["assigned_at"], "isoformat") else r["assigned_at"],
         "assigned_by": r["assigned_by"]}
        for r in rows]}


async def assign(session: AsyncSession, *, organization_id: UUID | str, vendor_id: str,
                 building_ids: list[str], user_id: object | None,
                 source: str = "manual") -> dict[str, Any]:
    """Assigns the vendor to each building (one already assigned is left as it is)."""
    if not await ready(session):
        return dict(NOT_SET_UP)
    org = str(organization_id)
    wanted = [b for b in dict.fromkeys(_canon_uuid(x) for x in (building_ids or [])) if b]
    if not wanted:
        return {"ok": False, "reason": "no_buildings", "error": "Choose at least one building."}
    refused = await vendor_link_refusal(session, vendor_id, org,
                                        what="it cannot be assigned to a building")
    if refused:
        return refused
    rows = (await session.execute(text(_BUILDINGS), {"org": org, "ids": wanted})).mappings().all()
    named = {r["building_id"]: r["name"] for r in rows}
    missing = [b for b in wanted if b not in named]
    if missing:
        return {"ok": False, "reason": "building_not_found",
                "error": "No such building in your company, so nothing was assigned."}
    src = source if source in SOURCES else "manual"
    uid = str(user_id) if user_id else None
    vendor_id = str((await session.execute(text(_CANON), {"vid": str(vendor_id), "org": org})).scalar() or vendor_id)
    added: list[dict[str, Any]] = []
    already: list[dict[str, Any]] = []
    for bid in wanted:
        n = (await session.execute(text(_INSERT), {
            "org": org, "vid": str(vendor_id), "bid": bid, "name": named[bid],
            "source": src, "uid": uid})).rowcount or 0
        (added if n else already).append({"id": bid, "name": named[bid]})
    if added:
        await write_audit(session, actor=uid or "hoistra-ui",
                          action_type=vendor_audit.BUILDINGS_ASSIGNED, source_feature=PLATFORM_FEATURE,
                          organization_id=UUID(org),
                          detail={"vendor": await _vendor_party(session, str(vendor_id), org),
                                  "buildings": added, "source": src})
    await session.commit()
    return {"ok": True, "assigned": added, "already": already}


async def unassign(session: AsyncSession, *, organization_id: UUID | str, vendor_id: str,
                   building_id: str, user_id: object | None) -> dict[str, Any]:
    """Ends the vendor's assignment to the building (kept as history)."""
    if not await ready(session):
        return dict(NOT_SET_UP)
    bid = _canon_uuid(building_id)
    if not bid:
        return {"ok": False, "reason": "building_not_found", "error": "No such building."}
    org = str(organization_id)
    uid = str(user_id) if user_id else None
    ended = (await session.execute(text(_END), {"org": org, "vid": str(vendor_id), "bid": bid, "uid": uid})).all()
    if not ended:
        await session.rollback()
        return {"ok": False, "reason": "not_assigned",
                "error": "That vendor is not assigned to that building."}
    await write_audit(session, actor=uid or "hoistra-ui",
                      action_type=vendor_audit.BUILDING_UNASSIGNED, source_feature=PLATFORM_FEATURE,
                      organization_id=UUID(org),
                      detail={"vendor": await _vendor_party(session, str(vendor_id), org),
                              "buildings": [{"id": bid, "name": ended[0][0]}]})
    await session.commit()
    return {"ok": True, "unassigned": bid}
