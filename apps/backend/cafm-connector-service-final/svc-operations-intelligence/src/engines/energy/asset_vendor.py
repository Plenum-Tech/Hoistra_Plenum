"""The vendor an asset is assigned to: what the Assets page's vendor drawer reads and changes.

The vendor on an asset is not a label. It decides who receives the asset's work-order,
inspection and records emails — which are sent for real — and whose scorecard its work counts
towards. So changing it is an admin's decision (Hussain, 28 Sep 2026: direct, confirmed, admins
only), it stays inside the asset's company, it refuses a vendor that is blocked or inactive, and
it is written together with its audit entry or not at all: a change nobody can trace is the one
thing this must never make.

Two databases disagree on vendors.id: one carries 1,025 legacy text ids (V-01, VEN-ABCMECH-001)
beside uuids, and every column that points at a vendor is uuid. A legacy vendor therefore cannot
be attached to an asset whose vendor_id is uuid; the drawer lists it as not assignable and says
why, rather than failing on the write.
"""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import asset_intelligence as ai

log = get_logger(__name__)

ACTION = "asset.vendor_changed"
NOTE_MAX = 500
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_ASSET = """
    SELECT a.id::text AS id, a.asset_name, to_jsonb(a)->>'asset_code' AS asset_code,
           a.vendor_id::text AS vendor_id, to_jsonb(a)->>'organization_id' AS organization_id
      FROM plenum_cafm.assets a WHERE a.id::text = :aid LIMIT 1"""

_VENDOR = """
    SELECT v.id::text AS id, v.vendor_name,
           to_jsonb(v)->>'vendor_code' AS vendor_code, to_jsonb(v)->>'trade' AS trade,
           to_jsonb(v)->>'accreditation' AS accreditation, to_jsonb(v)->>'block_state' AS block_state,
           to_jsonb(v)->>'block_reason' AS block_reason, to_jsonb(v)->>'phone' AS phone,
           to_jsonb(v)->>'status' AS status, to_jsonb(v)->>'organization_id' AS organization_id
      FROM plenum_cafm.vendors v WHERE v.id::text = :vid LIMIT 1"""

_CHOICES = """
    SELECT v.id::text AS id, v.vendor_name, to_jsonb(v)->>'trade' AS trade,
           to_jsonb(v)->>'block_state' AS block_state, to_jsonb(v)->>'block_reason' AS block_reason,
           to_jsonb(v)->>'status' AS status
      FROM plenum_cafm.vendors v
     WHERE to_jsonb(v)->>'organization_id' = :org
     ORDER BY v.vendor_name
     LIMIT 500"""

#: Vendors with a lapsed vendor-scope certificate, as the Compliance page counts them Blocked
#: ("block_state=Blocked OR any lapsed cert", certificates.py) — block_state itself only moves
#: when a scan or an upload runs, so between them a lapsed vendor reads Clear. The page's own
#: rule: vendor accreditation is any scope but "building", and superseded, archived and
#: test-fixture certificates never count (certificates.py KPI).
_LAPSED = """
    SELECT c.vendor_id::text AS vid,
           min(COALESCE(to_jsonb(c)->>'certificate_type_code', to_jsonb(c)->>'cert_type',
                        'accreditation')) AS type
      FROM plenum_cafm.compliance_certificates c
     WHERE c.vendor_id IS NOT NULL
       AND (to_jsonb(c)->>'organization_id' = :org OR to_jsonb(c)->>'org_id' = :org)
       AND lower(COALESCE(to_jsonb(c)->>'cert_scope', '')) <> 'building'
       AND (c.expiry_date <= CURRENT_DATE
            OR lower(COALESCE(to_jsonb(c)->>'status', '')) IN ('lapsed', 'expired'))
       AND COALESCE(to_jsonb(c)->'raw_metadata'->>'superseded_duplicate', 'false') NOT IN ('true', '1')
       AND COALESCE(to_jsonb(c)->'raw_metadata'->>'archived', 'false') NOT IN ('true', '1')
       AND COALESCE(to_jsonb(c)->'raw_metadata'->>'a1_test_fixture', 'false') NOT IN ('true', '1')
       {one}
     GROUP BY c.vendor_id"""

_SCORE = """
    SELECT to_jsonb(c)->>'overall_score' AS score, to_jsonb(c)->>'score_month' AS month
      FROM plenum_cafm.vendor_monthly_scorecards c
     WHERE c.vendor_id::text = :vid
       AND (to_jsonb(c)->>'organization_id' IS NULL OR to_jsonb(c)->>'organization_id' = :org)
     ORDER BY to_jsonb(c)->>'score_month' DESC
     LIMIT 1"""

_LAST_CHANGE = """
    SELECT l.created_at, l.metadata, u.email
      FROM plenum_cafm.audit_logs l
      LEFT JOIN plenum_cafm.users u ON u.id::text = l.user_id::text
     WHERE l.entity_type = 'asset' AND l.entity_id::text = :aid AND l.action = :action
     ORDER BY l.created_at DESC
     LIMIT 1"""

_COLUMN_TYPE = """
    SELECT data_type FROM information_schema.columns
     WHERE table_schema = 'plenum_cafm' AND table_name = 'assets' AND column_name = 'vendor_id'
     LIMIT 1"""

_AUDIT = """
    INSERT INTO plenum_cafm.audit_logs
        (id, organization_id, user_id, action, entity_type, entity_id, metadata, created_at)
    VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:u AS uuid), :action, 'asset',
            CAST(:aid AS uuid), :meta, now())
    RETURNING id::text AS id"""

#: assets.vendor_id's type, read once per process — and only a successful read is kept.
_COL_TYPE: str | None = None


def _num(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


async def _rows(session: AsyncSession, sql: str, params: dict, what: str) -> list[dict] | None:
    try:
        async with session.begin_nested():
            return [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001 — a part that cannot be read is named, not fatal
        log.warning("asset_vendor.read_failed", part=what, error=str(exc)[:200])
        return None


async def _column_type(session: AsyncSession) -> str:
    global _COL_TYPE
    if _COL_TYPE is None:
        rows = await _rows(session, _COLUMN_TYPE, {}, "column_type")
        if rows:
            _COL_TYPE = str(rows[0].get("data_type") or "")
        else:
            return "uuid"   # unknown: assume the stricter shape, never the looser one
    return _COL_TYPE


#: Words that name no trade: "Mechanical Services" is the Mechanical trade.
_TRADE_FILLER = frozenset({"services", "service", "contractor", "contractors", "ltd", "limited"})


def _trade_parts(trade: Any) -> set[str]:
    """The trades a vendor's trade field names — "Mechanical & Electrical" names two."""
    s = str(trade or "").lower()
    for sep in ("&", " and ", "/", "+", ";", "|"):
        s = s.replace(sep, ",")
    out = set()
    for part in s.split(","):
        words = [w for w in re.findall(r"[a-z0-9]+", part) if w not in _TRADE_FILLER]
        if words:
            out.add(" ".join(words))
    return out


def same_trade(a: Any, b: Any) -> bool:
    """Whether two trade fields share a trade. A vendor with no trade shares none."""
    return bool(_trade_parts(a) & _trade_parts(b))


def _blocked(v: dict) -> bool:
    return str(v.get("block_state") or "").strip().lower() == "blocked"


def _inactive(v: dict) -> bool:
    st = str(v.get("status") or "active").strip().lower()
    return st not in ("active", "")


def _iso_utc(v: Any) -> Any:
    """A timestamp as ISO text that says it is UTC. audit_logs.created_at is timestamp without
    time zone, written with now() in the server's UTC session; sent bare, the browser read it
    as local time and a change showed an hour early in BST."""
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None and hasattr(v, "replace"):
            from datetime import timezone
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()
    return v


def _why_not(v: dict, col_type: str, lapsed: str | None = None) -> str | None:
    """Why this vendor cannot be assigned, or None when it can. ``lapsed`` names the type of a
    lapsed certificate the vendor holds, if any."""
    if _blocked(v):
        return "blocked" + (f" — {v['block_reason']}" if v.get("block_reason") else "")
    if lapsed:
        return f"blocked — {lapsed} certificate lapsed"
    if _inactive(v):
        return f"not active ({v.get('status')})"
    if col_type == "uuid" and not _UUID.match(str(v.get("id") or "")):
        return ("carries a legacy id, which cannot be attached to an asset until the vendors-id "
                "migration")
    return None


def _other_company(org: Any, organization_id: Any) -> bool:
    """True when the asset carries a company and it is not the caller's. Access is granted by
    building; an asset whose own company differs from its building's must still not show one
    company another's vendors."""
    return bool(org and organization_id and str(org) != str(organization_id))


async def vendor_view(session: AsyncSession, *, asset_id: str, organization_id: Any = None,
                      include_choices: bool = True) -> dict[str, Any]:
    """Everything the vendor drawer shows for one asset. Reads only. ``include_choices`` is
    the caller's right to change it: only an admin is shown the company's vendor list."""
    unreadable: list[str] = []
    asset_rows = await _rows(session, _ASSET, {"aid": str(asset_id)}, "asset")
    if not asset_rows:
        return {"ok": False, "reason": "not_found"}
    a = asset_rows[0]
    org = a.get("organization_id")
    if _other_company(org, organization_id):
        return {"ok": False, "reason": "not_found"}
    col_type = await _column_type(session)

    v = None
    if a.get("vendor_id"):
        vrows = await _rows(session, _VENDOR, {"vid": a["vendor_id"]}, "vendor")
        if vrows is None:
            unreadable.append("the vendor record")
        elif vrows:
            v = vrows[0]
    contacts = await ai.vendor_contacts(session, a["vendor_id"]) if v else {"email": None, "candidates": []}

    score = None
    if v:
        srows = await _rows(session, _SCORE, {"vid": v["id"], "org": org}, "scorecard")
        if srows is None:
            unreadable.append("the vendor's scorecard")
        elif srows and _num(srows[0].get("score")) is not None:
            score = {"score": _num(srows[0]["score"]), "month": str(srows[0].get("month") or "")[:10]}

    last = None
    lrows = await _rows(session, _LAST_CHANGE, {"aid": a["id"], "action": ACTION}, "audit")
    if lrows is None:
        unreadable.append("the change history")
    elif lrows:
        r = lrows[0]
        try:
            meta = json.loads(r.get("metadata") or "{}")
        except ValueError:
            meta = {}
        last = {"at": _iso_utc(r.get("created_at")),
                "by": r.get("email"), "from": (meta.get("from") or {}).get("name"),
                "to": (meta.get("to") or {}).get("name"), "note": meta.get("note")}

    choices: list[dict[str, Any]] = []
    crows = await _rows(session, _CHOICES, {"org": org}, "choices") if org and include_choices else []
    if crows is None:
        unreadable.append("the company's vendors")
    lapsed: dict[str, str] = {}
    if crows:
        lrows = await _rows(session, _LAPSED.format(one=""), {"org": org}, "lapsed certificates")
        if lrows is None:
            unreadable.append("the vendors' accreditations")
        lapsed = {r["vid"]: r.get("type") or "accreditation" for r in (lrows or [])}
    # Only vendors of the current vendor's trade (Hussain, 29 Sep 2026): a boiler held by a
    # mechanical contractor moves to another mechanical contractor, not to the electrician. A
    # current vendor with no trade on record — or no vendor at all — filters nothing, and says so.
    trade = v.get("trade") if v else None
    applied = bool(_trade_parts(trade))
    kept = [c for c in (crows or [])
            if not applied or (v and c["id"] == v["id"]) or same_trade(c.get("trade"), trade)]
    trade_filter = {"trade": trade if applied else None, "applied": applied,
                    "hidden": len(crows or []) - len(kept)}
    for c in kept:
        why = _why_not(c, col_type, lapsed.get(c["id"]))
        choices.append({"id": c["id"], "name": c["vendor_name"], "trade": c.get("trade"),
                        "assignable": why is None, "why": why,
                        "current": bool(v) and c["id"] == v["id"]})

    return {
        "ok": True,
        "asset": {"id": a["id"], "name": a["asset_name"], "code": a.get("asset_code")},
        "vendor": ({"id": v["id"], "name": v["vendor_name"], "code": v.get("vendor_code"),
                    "trade": v.get("trade"), "accreditation": v.get("accreditation"),
                    "block_state": v.get("block_state") or "Clear", "block_reason": v.get("block_reason"),
                    "phone": v.get("phone"), "status": v.get("status")} if v else None),
        "contacts": contacts, "score": score, "last_change": last,
        "choices": choices, "trade_filter": trade_filter, "unreadable": unreadable,
    }


async def change_vendor(session: AsyncSession, *, asset_id: str, vendor_id: str, user_id: UUID | None,
                        role: str, note: str | None = None, organization_id: Any = None) -> dict[str, Any]:
    """Assign the asset to another of its company's vendors, audited in the same transaction."""
    from ..auth import roles as role_engine

    if not role_engine.at_least(role, role_engine.ADMIN):
        return {"ok": False, "reason": "not_admin",
                "error": "Only an admin can change the vendor an asset is assigned to."}
    asset_rows = await _rows(session, _ASSET, {"aid": str(asset_id)}, "asset")
    if not asset_rows:
        return {"ok": False, "reason": "not_found", "error": "No such asset in your buildings."}
    a = asset_rows[0]
    org = a.get("organization_id")
    if _other_company(org, organization_id):
        return {"ok": False, "reason": "not_found", "error": "No such asset in your buildings."}
    vrows = await _rows(session, _VENDOR, {"vid": str(vendor_id)}, "vendor")
    v = vrows[0] if vrows else None
    # Another company's vendor is answered exactly like one that does not exist.
    if not v or not org or v.get("organization_id") != org:
        return {"ok": False, "reason": "vendor_not_found", "error": "No such vendor in this company."}
    if a.get("vendor_id") == v["id"]:
        return {"ok": True, "changed": False, "vendor": {"id": v["id"], "name": v["vendor_name"]}}
    prow = None
    if a.get("vendor_id"):
        prows = await _rows(session, _VENDOR, {"vid": a["vendor_id"]}, "previous_vendor")
        prow = prows[0] if prows else None
    if _blocked(v):
        return {"ok": False, "reason": "blocked",
                "error": f"{v['vendor_name']} is blocked" + (f": {v['block_reason']}" if v.get("block_reason") else "")
                         + ". A blocked vendor cannot be assigned."}
    if _inactive(v):
        return {"ok": False, "reason": "inactive", "error": f"{v['vendor_name']} is not active ({v.get('status')})."}
    # Blocked the way the Compliance page counts it — a lapsed certificate blocks between scans.
    # A check that cannot be made refuses: the new vendor starts receiving real emails.
    lrows = await _rows(session, _LAPSED.format(one="AND c.vendor_id::text = :vid"),
                        {"org": org, "vid": v["id"]}, "lapsed certificates")
    if lrows is None:
        return {"ok": False, "reason": "unchecked",
                "error": f"Not changed — {v['vendor_name']}'s accreditations could not be checked just now."}
    if lrows:
        kind = lrows[0].get("type") or "accreditation"
        return {"ok": False, "reason": "blocked",
                "error": f"{v['vendor_name']}'s {kind} certificate has lapsed. A vendor with a lapsed "
                         "accreditation cannot be assigned."}
    # The same rule the drawer's list applies, so a request made by hand cannot get round it.
    if prow and _trade_parts(prow.get("trade")) and not same_trade(v.get("trade"), prow.get("trade")):
        return {"ok": False, "reason": "different_trade",
                "error": (f"{v['vendor_name']}'s trade ({v.get('trade') or 'none on record'}) is not "
                          f"{prow['vendor_name']}'s ({prow.get('trade')}). Only a vendor of the same "
                          "trade can take over this asset.")}
    col_type = await _column_type(session)
    if col_type == "uuid" and not _UUID.match(v["id"]):
        return {"ok": False, "reason": "legacy_id",
                "error": (f"{v['vendor_name']} carries a legacy id ({v['id']}), which cannot be attached "
                          "to an asset until the vendors-id migration.")}

    prev = {"id": a["vendor_id"], "name": prow["vendor_name"] if prow else None} if a.get("vendor_id") else None
    meta = {"asset": {"id": a["id"], "name": a["asset_name"], "code": a.get("asset_code")},
            "from": prev, "to": {"id": v["id"], "name": v["vendor_name"]},
            "note": (note or "").strip()[:NOTE_MAX] or None, "by_role": role}
    target = "CAST(:vid AS uuid)" if col_type == "uuid" else ":vid"
    step = "update"
    try:
        # One savepoint for both: the change and its audit entry land together or not at all.
        async with session.begin_nested():
            res = await session.execute(text(f"""
                UPDATE plenum_cafm.assets SET vendor_id = {target}, updated_at = now()
                 WHERE id::text = :aid"""), {"vid": v["id"], "aid": a["id"]})
            if getattr(res, "rowcount", 1) != 1:
                raise RuntimeError("the asset row was not updated")
            step = "audit"
            audit_id = (await session.execute(text(_AUDIT), {
                "o": org, "u": str(user_id) if user_id else None, "action": ACTION,
                "aid": a["id"], "meta": json.dumps(meta, default=str)})).scalar()
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — both are undone; the reader is told which part failed
        log.warning("asset_vendor.change_failed", step=step, error=str(exc)[:200])
        await session.rollback()
        if step == "audit":
            return {"ok": False, "reason": "not_recorded",
                    "error": "Not changed — the change could not be recorded in the audit log, so it was rolled back."}
        return {"ok": False, "reason": "not_changed", "error": "Not changed — the asset could not be updated."}
    log.info("asset_vendor.changed", asset_id=a["id"], to=v["id"], previous=(prev or {}).get("id"))
    return {"ok": True, "changed": True, "vendor": {"id": v["id"], "name": v["vendor_name"]},
            "previous": prev, "audit_id": audit_id}
