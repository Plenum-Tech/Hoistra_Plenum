"""The vendor audit trail: which vendor changed, how, when, and who or what changed it.

Four kinds of change, read from the two places they are recorded:

    reassigned   an asset moved from one vendor to another — plenum_cafm.audit_logs, written
                 by the vendor drawer with the admin who made it (energy/asset_vendor.py)
    blocked      compliance blocked a vendor for a lapsed accreditation
    cleared      a block was lifted — by a scan, or by a newer certificate
    deleted      an admin deleted a vendor (admin/vendor_delete.py), or a data reset deleted
                 several — what it removed, unassigned, closed and kept, and who did it

Blocks and clears were recorded nowhere before 29 Sep 2026: the scan overwrote block_reason
and block_date on every run and a clear nulled them. They are appended to ops_audit_log
(append-only by trigger) from then on, once per change of state — a vendor re-blocked by
every nightly scan is one entry, not one a night. Earlier blocks cannot be shown.

Deletes are recorded in ops_audit_log from 12 Oct 2026 (Hussain: delete a vendor, recorded
here). A delete that unassigns assets also writes one audit_logs row per asset, so each asset's
own history says so; the trail lists those assets on the one delete entry instead of showing a
row per asset beside it. An entry whose action this module does not know is left out rather
than shown as something it is not.

Nothing here is ever updated or deleted: an audit entry that can be edited is not one.
"""
from __future__ import annotations

import json
from datetime import timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

REASSIGNED = "asset.vendor_changed"  # energy/asset_vendor.py ACTION
BLOCKED = "vendor.blocked"
CLEARED = "vendor.block_cleared"
#: An admin deleted a vendor (admin/vendor_delete.py), or a data reset deleted several.
DELETED = "vendor.deleted"
#: An asset lost its vendor because that vendor was deleted — one audit_logs row per asset,
#: so the asset's own history says so; the trail shows the delete event that lists them.
UNASSIGNED = "asset.vendor_unassigned"
#: An admin assigned a vendor to buildings, or ended one (compliance/vendor_buildings.py).
BUILDINGS_ASSIGNED = "vendor.buildings_assigned"
BUILDING_UNASSIGNED = "vendor.building_unassigned"

_REASSIGNMENTS = """
    SELECT l.id::text AS id, l.created_at, l.metadata, l.user_id::text AS user_id,
           u.full_name AS actor_name, u.email AS actor_email,
           to_jsonb(u)->>'platform_role' AS actor_role,
           b.building_id::text AS building_id, b.name AS building_name
      FROM plenum_cafm.audit_logs l
      LEFT JOIN plenum_cafm.users u ON u.id::text = l.user_id::text
      LEFT JOIN plenum_cafm.assets a ON a.id::text = l.entity_id::text
      LEFT JOIN plenum_cafm.buildings b ON b.building_id::text = to_jsonb(a)->>'building_id'
     WHERE l.organization_id::text = :org AND l.action = :action
     ORDER BY l.created_at DESC
     LIMIT :lim"""

#: Blocks, clears and deletes. actor is free text — str(user_id) for a person, "system:<source>"
#: for a scan — so a delete's admin is joined on it the way a reassignment's is on user_id.
_BLOCKS = """
    SELECT o.id::text AS id, o.created_at, o.action_type, o.actor, o.detail,
           u.id::text AS actor_user_id, u.full_name AS actor_name, u.email AS actor_email,
           to_jsonb(u)->>'platform_role' AS actor_role
      FROM plenum_cafm.ops_audit_log o
      LEFT JOIN plenum_cafm.users u ON u.id::text = o.actor
     WHERE o.organization_id::text = :org AND o.action_type = ANY(:actions)
     ORDER BY o.created_at DESC
     LIMIT :lim"""


def _json(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    try:
        out = json.loads(v) if v else {}
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _party(v: Any) -> dict | None:
    """{id, name} of a vendor or asset as recorded, or None when the entry does not say."""
    if not isinstance(v, dict) or not (v.get("id") or v.get("name")):
        return None
    out = {"id": v.get("id"), "name": v.get("name")}
    if "code" in v:
        out["code"] = v.get("code")
    return out


def _iso(v: Any) -> str | None:
    """ISO text that says it is UTC. audit_logs.created_at is timestamp without time zone
    (written with now() in the server's UTC session); sent bare, the browser read it as local
    time and a reassignment showed an hour early in BST, out of order with the blocks."""
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None and hasattr(v, "replace") and hasattr(v, "hour"):
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return str(v) if v else None


async def record_block_event(
    session: AsyncSession,
    *,
    action: str,
    vendor_id: Any,
    vendor_name: str | None,
    organization_id: Any,
    source: str,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Append one block/clear entry in its own savepoint. Never raises and never undoes the
    block it records: a vendor stays blocked whether or not the entry could be written."""
    from ...shared.approvals import write_audit

    try:
        org = UUID(str(organization_id)) if organization_id else None
    except ValueError:
        org = None
    body = {"vendor": {"id": str(vendor_id), "name": vendor_name}, **(detail or {}), "source": source}
    try:
        async with session.begin_nested():
            await write_audit(session, actor=f"system:{source}", action_type=action,
                              source_feature="A", organization_id=org, detail=body)
        return True
    except Exception as exc:  # noqa: BLE001 — see docstring
        log.warning("vendor_audit.not_recorded", action=action, vendor_id=str(vendor_id),
                    error=str(exc)[:200])
        return False


_STATES = """
    SELECT v.id::text AS id, COALESCE(v.block_state, 'Clear') AS block_state, v.vendor_name,
           v.organization_id::text AS organization_id, v.block_reason,
           to_jsonb(v)->>'blocked_accreditation_type' AS blocked_accreditation_type
      FROM plenum_cafm.vendors v
     WHERE v.id::text = ANY(:ids)"""


def states_by_vendor(rows: list[dict]) -> dict[str, dict]:
    return {str(r["id"]): dict(r) for r in rows or [] if r.get("id")}


async def read_vendor_states(session: AsyncSession, ids: list[str]) -> dict[str, dict]:
    """Block state per vendor, for a scan to compare before and after its run. {} when it
    cannot be read — the scan then records nothing rather than guessing."""
    if not ids:
        return {}
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(_STATES), {"ids": list(ids)})).mappings().all()
        return states_by_vendor([dict(r) for r in rows])
    except Exception as exc:  # noqa: BLE001
        log.warning("vendor_audit.states_unreadable", error=str(exc)[:200])
        return {}


async def record_net_changes(session: AsyncSession, before: dict[str, dict],
                             after: dict[str, dict], *, source: str) -> int:
    """One entry per vendor whose block state differs between ``before`` and ``after``.

    The scan runs the ladder once per CERTIFICATE, so a vendor holding a lapsed and a valid
    certificate is blocked and cleared inside one run; recording each call wrote two entries a
    night for a vendor that ended where it started. The run's net change is the change."""
    n = 0
    for vid, a in after.items():
        b = before.get(vid)
        if not b or (b.get("block_state") == "Blocked") == (a.get("block_state") == "Blocked"):
            continue
        blocked = a.get("block_state") == "Blocked"
        detail = ({"reason": a.get("block_reason"), "accreditation_type": a.get("blocked_accreditation_type")}
                  if blocked else {"accreditation_type": b.get("blocked_accreditation_type")})
        if await record_block_event(session, action=BLOCKED if blocked else CLEARED, vendor_id=vid,
                                    vendor_name=a.get("vendor_name"),
                                    organization_id=a.get("organization_id"), source=source,
                                    detail=detail):
            n += 1
    return n


async def _read(session: AsyncSession, sql: str, params: dict, what: str,
                unreadable: list[str]) -> list[dict]:
    try:
        async with session.begin_nested():
            return [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001 — a record that cannot be read is named, not fatal
        log.warning("vendor_audit.read_failed", part=what, error=str(exc)[:200])
        unreadable.append(what)
        return []


def _reassignment(r: dict) -> dict:
    meta = _json(r.get("metadata"))
    to = _party(meta.get("to"))
    return {
        "id": "r:" + str(r.get("id")), "kind": "reassigned", "at": _iso(r.get("created_at")),
        "actor": ({"id": r.get("user_id"), "name": r.get("actor_name"), "email": r.get("actor_email"),
                   "role": r.get("actor_role") or meta.get("by_role")}
                  if (r.get("user_id") or r.get("actor_name") or r.get("actor_email")) else None),
        "source": "person",
        "vendor": to, "from_vendor": _party(meta.get("from")), "to_vendor": to,
        "asset": _party(meta.get("asset")),
        "building": ({"id": r.get("building_id"), "name": r.get("building_name")}
                     if r.get("building_name") else None),
        "note": meta.get("note"), "reason": None, "accreditation": None, "certificate_id": None,
    }


def _block(r: dict, kind: str) -> dict:
    d = _json(r.get("detail"))
    actor = str(r.get("actor") or "")
    source = d.get("source") or (actor.split(":", 1)[1] if actor.startswith("system:") else "compliance_scan")
    return {
        "id": "o:" + str(r.get("id")),
        "kind": kind,
        "at": _iso(r.get("created_at")), "actor": None, "source": source,
        "vendor": _party(d.get("vendor")), "from_vendor": None, "to_vendor": None,
        "asset": None, "building": None, "note": None,
        "reason": d.get("reason"), "accreditation": d.get("accreditation_type"),
        "certificate_id": d.get("certificate_id"),
    }


def _user_id(actor: Any) -> str | None:
    """The actor when it is a user's id. A scan's "system:<source>", or a client label some old
    writers passed, names no one."""
    try:
        return str(UUID(str(actor)))
    except (TypeError, ValueError):
        return None


def _dicts(v: Any) -> list[dict]:
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _deleted(r: dict) -> dict:
    """A delete as recorded. A data reset deletes several vendors in one entry and lists them
    in ``vendors``.

    Who did it is the users row the entry's actor names. 12 Oct 2026 (review R2): it used to
    be the entry's own snapshot (``by``) first, but ops_audit_log takes inserts from generic
    writers and a snapshot is free JSON, so a made-up row could put anyone's name on a delete.
    The snapshot now speaks only for the actor the entry carries: its role at the time when it
    names that same user, and the whole identity only when no users row matches (a user since
    removed). A delete with neither has no actor."""
    d = _json(r.get("detail"))
    by = d.get("by") if isinstance(d.get("by"), dict) else {}
    uid = _user_id(r.get("actor"))
    same = bool(uid) and str(by.get("id") or "").lower() == uid
    if r.get("actor_user_id"):
        actor = {"id": str(r.get("actor_user_id")), "name": r.get("actor_name"),
                 "email": r.get("actor_email"),
                 "role": (by.get("role") if same else None) or r.get("actor_role")}
    elif same:
        actor = {"id": uid, "name": by.get("name"), "email": by.get("email"),
                 "role": by.get("role")}
    else:
        actor = {"id": uid, "name": None, "email": None, "role": None}
    count = d.get("count")
    return {
        "id": "o:" + str(r.get("id")), "kind": "deleted", "at": _iso(r.get("created_at")),
        "actor": actor if (actor["id"] or actor["name"] or actor["email"]) else None,
        "source": d.get("source") or "person",
        "vendor": _party(d.get("vendor")),
        "mode": d.get("mode"), "reason": d.get("reason"),
        "effects": d.get("effects") if isinstance(d.get("effects"), dict) else {},
        "assets": _dicts(d.get("assets")), "work_orders": _dicts(d.get("work_orders")),
        "vendors": ([p for p in (_party(x) for x in d["vendors"]) if p]
                    if isinstance(d.get("vendors"), list) else None),
        "count": count if isinstance(count, int) and not isinstance(count, bool) else None,
        # What this database could not be checked for when the delete ran (vendor_delete.py).
        "unavailable": [str(x) for x in d.get("unavailable") or [] if x],
        "from_vendor": None, "to_vendor": None, "asset": None, "building": None, "note": None,
        "accreditation": None, "certificate_id": None,
    }


def _buildings(r: dict, kind: str) -> dict:
    """A vendor assigned to buildings, or taken off one. Who did it is the users row the actor
    names; the buildings are as the entry recorded them."""
    d = _json(r.get("detail"))
    uid = _user_id(r.get("actor"))
    actor = ({"id": str(r.get("actor_user_id")), "name": r.get("actor_name"),
              "email": r.get("actor_email"), "role": r.get("actor_role")}
             if r.get("actor_user_id") else {"id": uid, "name": None, "email": None, "role": None})
    return {
        "id": "o:" + str(r.get("id")), "kind": kind, "at": _iso(r.get("created_at")),
        "actor": actor if (actor["id"] or actor["name"] or actor["email"]) else None,
        "source": d.get("source") or "manual", "vendor": _party(d.get("vendor")),
        "buildings": [p for p in (_party(x) for x in _dicts(d.get("buildings"))) if p],
        "from_vendor": None, "to_vendor": None, "asset": None, "building": None, "note": None,
        "accreditation": None, "certificate_id": None,
    }


def _ops_event(r: dict) -> dict | None:
    """One ops_audit_log entry as a trail event, or None for an action this module does not
    know. Every entry that was not a block used to read as "cleared", so an action added to the
    read without its own mapping would have shown as a lifted block."""
    action = r.get("action_type")
    if action == BLOCKED:
        return _block(r, "blocked")
    if action == CLEARED:
        return _block(r, "cleared")
    if action == DELETED:
        return _deleted(r)
    if action == BUILDINGS_ASSIGNED:
        return _buildings(r, "assigned")
    if action == BUILDING_UNASSIGNED:
        return _buildings(r, "unassigned")
    log.warning("vendor_audit.unknown_action", action=str(action)[:100], id=str(r.get("id")))
    return None


async def list_events(session: AsyncSession, *, organization_id: UUID, limit: int = 300) -> dict:
    """The company's vendor changes, newest first. A record that cannot be read is named in
    ``unreadable`` and the other still shows."""
    lim = max(1, min(int(limit), 1000))
    org = str(organization_id)
    unreadable: list[str] = []
    moves = await _read(session, _REASSIGNMENTS, {"org": org, "action": REASSIGNED, "lim": lim},
                        "vendor reassignments", unreadable)
    blocks = await _read(session, _BLOCKS,
                         {"org": org, "actions": [BLOCKED, CLEARED, DELETED, BUILDINGS_ASSIGNED,
                                                  BUILDING_UNASSIGNED], "lim": lim},
                         "blocks, clears, deletions and building assignments", unreadable)
    events = [_reassignment(r) for r in moves] + [e for e in map(_ops_event, blocks) if e]
    events.sort(key=lambda e: e["at"] or "", reverse=True)
    return {"ok": True, "events": events[:lim], "unreadable": unreadable}
