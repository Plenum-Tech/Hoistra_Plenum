"""SLA facts for work orders, worked out from the records rather than left to the model (2 Nov 2026).

"Did WO-B-301-4564 meet its response SLA?" was answered "met" by comparing its attendance with
sla_due_at - which is the COMPLETION (rectification) due time - while the job sat open 34 days past
it. And "how has Kestamere been performing at this building?" had only the vendor-wide scorecard,
though 325 of its work orders at Bishopsgate Tower are on record with a per-order score.

Two reads, both read-only and bounded to the caller's company (and a restricted caller's buildings):

    sla_check(work_order)                    one work order: response and completion, each on its own
    vendor_at_building(vendor, building)     a vendor's work orders at a building, with SLA results

Response is judged from the work order's score (vendor_wo_scores.sla_response_met, scored against
the vendor's contract terms or the platform default), never from sla_due_at. Completion is
completed_at against sla_due_at, or overdue when the order is still open past it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from ..services.principal import VENDORS_ON_BUILDINGS_SQL, building_clause, caller_principal

log = structlog.get_logger(__name__)

_OPEN_DONE = ("completed", "closed", "cancelled", "canceled")


def _scope() -> str | None:
    """The caller's company as uuid text; its buildings narrow each read through building_clause."""
    from ..http_client import caller_organization_id
    from ..services.principal import caller_principal

    p = caller_principal.get(None)
    org = caller_organization_id.get() or (str(p.organization_id) if p and getattr(p, "organization_id", None) else None)
    try:
        org = str(uuid.UUID(str(org))) if org else None
    except ValueError:
        org = None
    return org


async def _rows(sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from .. import database

    if database.AsyncSessionLocal is None:
        database.init_session_factory()
    async with database.AsyncSessionLocal() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]


def _aware(v: Any) -> datetime | None:
    if not isinstance(v, datetime):
        return None
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def _minutes(a: Any, b: Any) -> int | None:
    a, b = _aware(a), _aware(b)
    return int((b - a).total_seconds() // 60) if a and b else None


def judge(row: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Response and completion for one work order row, each judged on its own."""
    now = now or datetime.now(timezone.utc)
    reported = row.get("reported_at")
    first = row.get("responded_at") or row.get("attended_at")
    scored = row.get("sla_response_met")
    response = {
        "reported_at": str(reported) if reported else None,
        "first_response_at": str(first) if first else None,
        "minutes_to_respond": _minutes(reported, first),
        "met": scored if isinstance(scored, bool) else None,
        "basis": ("scored against the vendor's contract terms or the platform default"
                  if isinstance(scored, bool) else "not scored yet: no response target recorded for this order"),
    }
    due, done = _aware(row.get("sla_due_at")), _aware(row.get("completed_at"))
    status = str(row.get("status") or "")
    open_ = status.lower() not in _OPEN_DONE and not done
    completion: dict[str, Any] = {"due_at": str(row.get("sla_due_at")) if due else None,
                                  "completed_at": str(row.get("completed_at")) if done else None}
    if done and due:
        completion["met"] = done <= due
    elif open_ and due and now > due:
        completion.update(met=False, overdue_days=(now - due).days, still_open=True)
    elif open_ and due:
        completion.update(met=None, still_open=True, due_in_days=(due - now).days)
    else:
        completion["met"] = row.get("sla_completion_met") if isinstance(row.get("sla_completion_met"), bool) else None
    return {"work_order": row.get("wo_code"), "status": status, "priority": row.get("priority"),
            "response": response, "completion": completion,
            "note": "sla_due_at is the completion (rectification) due time - never the response target."}


_WO_COLUMNS = ("w.wo_code, w.status, w.priority, w.reported_at, w.responded_at, w.attended_at, w.sla_due_at, "
               "w.completed_at, s.sla_response_met, s.sla_completion_met, s.first_fix, s.recall")
#: The work order stands at the building named :bname (an empty building code matches nothing).
AT_BUILDING = ("EXISTS (SELECT 1 FROM plenum_cafm.buildings b WHERE b.organization_id::text = :org "
               "AND b.name = :bname AND (w.building_id::text = b.building_id::text "
               "OR (COALESCE(w.building_code, '') <> '' AND w.building_code = b.building_code)))")
_JOIN_SCORE = ("LEFT JOIN LATERAL (SELECT sc.sla_response_met, sc.sla_completion_met, sc.first_fix, sc.recall "
               "FROM plenum_cafm.vendor_wo_scores sc WHERE sc.work_order_id = w.id "
               "ORDER BY sc.score_month DESC NULLS LAST LIMIT 1) s ON TRUE")


async def sla_check(work_order: str) -> dict[str, Any] | None:
    """One work order's response and completion SLA, by its code or id; None when not on record."""
    org = _scope()
    key = str(work_order or "").strip()
    if not org or not key:
        return None
    sql = (f"SELECT {_WO_COLUMNS} FROM plenum_cafm.work_orders w {_JOIN_SCORE} "
           "WHERE w.organization_id::text = :org AND (upper(w.wo_code) = upper(:k) OR w.id::text = :k)")
    params: dict[str, Any] = {"org": org, "k": key}
    # The caller's buildings (services/principal.building_clause): a user allocated to none reads none.
    clause, extra = building_clause("w.building_id")
    sql += clause
    params.update(extra)
    try:
        rows = await _rows(sql + " LIMIT 1", params)
    except Exception as exc:  # noqa: BLE001 - a check that cannot be read is left out, not raised
        log.warning("wo_sla.check_failed", error=str(exc)[:200])
        return None
    return judge(rows[0]) if rows else None


def vendor_clause(alias: str = "v", param: str = "scope_vendors") -> tuple[str, dict]:
    """A restricted caller's vendors: those with a footprint on their buildings (certificates, work
    orders, invoices - services/principal.VENDORS_ON_BUILDINGS_SQL). ("", {}) for an admin or when
    no caller is set; " AND FALSE" for a user allocated to no building."""
    p = caller_principal.get(None)
    ids = getattr(p, "building_ids", None) if p is not None else None
    if ids is None:
        return "", {}
    if not ids:
        return " AND FALSE", {}
    return (f" AND {alias}.id::text IN (SELECT vendor_id::text FROM {VENDORS_ON_BUILDINGS_SQL.format(p=param)} x)",
            {param: [str(b) for b in ids]})


#: The narrowing each name lookup carries for a restricted caller, so a "several match" reply can
#: never name a building or vendor outside their allocation (pre-push review, 3 Nov 2026).
_NAME_SCOPE = {"buildings": lambda: building_clause("building_id", param="scope_names"),
               "vendors": lambda: vendor_clause("t", param="scope_names")}


async def _one_name(table: str, column: str, typed: str, org: str, kind: frozenset[str]) -> tuple[str | None, list[str]]:
    from . import name_match

    clause, extra = _NAME_SCOPE[table]() if table in _NAME_SCOPE else ("", {})
    rows = await _rows(f"SELECT DISTINCT {column} AS n FROM plenum_cafm.{table} t WHERE t.organization_id::text = :org "
                       f"AND {column} IS NOT NULL" + clause, {"org": org, **extra})
    names = [(str(r["n"]), str(r["n"])) for r in rows if r.get("n")]
    found = [n for n, _ in name_match.best_matches(typed, names, kind)] if name_match.meaningful(typed, kind) else []
    return (found[0] if len(found) == 1 else None), found


async def vendor_at_building(vendor: str, building: str) -> dict[str, Any]:
    """A vendor's work orders at one building, with their response and completion SLA results."""
    org = _scope()
    if not org:
        return {"ok": False, "error": "No company to read work orders for."}
    try:
        vname, vfound = await _one_name("vendors", "vendor_name", vendor, org,
                                        frozenset({"vendor", "vendors", "contractor", "contractors", "supplier"}))
        bname, bfound = await _one_name("buildings", "name", building, org,
                                        frozenset({"building", "buildings", "site", "property"}))
        if not vname or not bname:
            return {"ok": False, "found": False,
                    "error": (f"vendor {vendor!r}: {vfound or 'no match'}; building {building!r}: {bfound or 'no match'}"),
                    "answer_hint": "Say which vendor or building did not match, or ask which one was meant."}
        # EXISTS, not JOIN: a building or vendor named twice in the register would otherwise
        # repeat every work order. Ids compared as text: vendors.id is varchar on production and
        # uuid on the test database, work-order vendor ids uuid (pre-push review, 3 Nov 2026).
        sql = (f"SELECT {_WO_COLUMNS} FROM plenum_cafm.work_orders w {_JOIN_SCORE} "
               f"WHERE w.organization_id::text = :org AND {AT_BUILDING} "
               "AND (lower(COALESCE(w.vendor_name, w.vendor, '')) = lower(:vname) "
               "OR EXISTS (SELECT 1 FROM plenum_cafm.vendors v WHERE v.organization_id::text = :org "
               "AND v.vendor_name = :vname AND (w.vendor_id::text = v.id::text OR w.assigned_vendor::text = v.id::text)))")
        params: dict[str, Any] = {"org": org, "bname": bname, "vname": vname}
        clause, extra = building_clause("w.building_id")
        sql += clause
        params.update(extra)
        rows = await _rows(sql, params)
    except Exception as exc:  # noqa: BLE001
        log.warning("wo_sla.vendor_at_building_failed", error=str(exc)[:200])
        return {"ok": False, "error": "The work orders could not be read."}
    return summarise(vname, bname, rows)


def summarise(vendor: str, building: str, rows: list[dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    resp = {"met": 0, "breached": 0, "not_scored": 0}
    comp = {"met": 0, "breached": 0, "open_overdue": 0, "open_in_time": 0, "unknown": 0}
    first_fix = recall = 0
    overdue: list[str] = []
    for r in rows:
        st = str(r.get("status") or "unknown")
        by_status[st] = by_status.get(st, 0) + 1
        j = judge(r, now)
        m = j["response"]["met"]
        resp["met" if m is True else "breached" if m is False else "not_scored"] += 1
        c = j["completion"]
        if c.get("still_open") and c.get("met") is False:
            comp["open_overdue"] += 1
            overdue.append(str(r.get("wo_code")))
        elif c.get("still_open"):
            comp["open_in_time"] += 1
        else:
            comp["met" if c.get("met") is True else "breached" if c.get("met") is False else "unknown"] += 1
        first_fix += 1 if r.get("first_fix") is True else 0
        recall += 1 if r.get("recall") is True else 0
    scored = resp["met"] + resp["breached"]
    return {
        "ok": True, "vendor": vendor, "building": building, "work_orders": len(rows), "by_status": by_status,
        "response_sla": {**resp, "met_rate_pct": round(100 * resp["met"] / scored, 1) if scored else None},
        "completion_sla": comp, "open_overdue": overdue[:20], "first_fix": first_fix, "recalls": recall,
        "answer_rules": ("Report this vendor's record at this building from these counts: how many orders, by status, "
                         "how many met and breached the response SLA (scored per order), the completion SLA, and any "
                         "open order past its due time. Do not use a vendor-wide scorecard for a building question."),
    }
