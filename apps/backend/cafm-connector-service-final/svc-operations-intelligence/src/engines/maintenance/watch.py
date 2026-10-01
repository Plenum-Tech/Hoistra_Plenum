"""Maintenance watch - the checks Hoist Crons runs over work orders, PPM plans and visits, and stock.

Five checks, each a read of the company's maintenance tables that reports what it found and
puts each finding a person must act on in the Approvals queue (source feature "M"):

    sla_watch        open work orders past their SLA (and those due within the horizon)
    ppm_due          maintenance plans overdue or due soon, with no open work order for them
    ppm_missed       PPM visits marked Missed or Deferred and not since rebooked
    parts_reorder    spare parts at or below their reorder level
    monthly_summary  last month's work orders, SLA hit rate and PPM completion, by building and vendor

Nothing here creates a work order or a purchase order: approving an item records that a person
took it on. A finding is raised once - while its item is pending, a later run does not raise it
again - and when a later run no longer finds it (the order closed, the visit rebooked, the
stock refilled) its pending item is cleared with a note saying so. A run for one building
clears only that building's items.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ..auth import access

log = get_logger(__name__)

SOURCE = "M"
RAISED_BY = "hoist_crons.maintenance"
#: A work order in one of these states is finished; any other state is open.
DONE_STATES = ("completed", "complete", "closed", "cancelled", "canceled", "rejected", "void")
#: Findings listed in a report; the counts are always whole.
REPORT_LINES = 15
#: Below these, a vendor's month is raised for review.
SLA_TARGET_PCT = 80.0
PPM_TARGET_PCT = 90.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scope_sql(building_ids: tuple[UUID, ...] | None, *, building_col: str | None = None,
               asset_col: str | None = None, code_col: str | None = None) -> tuple[str, dict[str, Any]]:
    """Narrow a maintenance read to some buildings: by its own building id, its asset's
    building, or its building code - whichever links the table has. None = unrestricted."""
    if building_ids is None:
        return "", {}
    ids = [str(b) for b in building_ids]
    if not ids:
        return " AND FALSE", {}
    parts = []
    if building_col:
        parts.append(f"{building_col} = ANY(CAST(:mw_bids AS uuid[]))")
    if asset_col:
        parts.append(f"{asset_col}::text IN " + access.ASSETS_ON_BUILDINGS_SQL.format(key="mw_bids"))
    if code_col:
        parts.append(f"{code_col} IN (SELECT b.building_code FROM plenum_cafm.buildings b"
                     " WHERE b.building_id = ANY(CAST(:mw_bids AS uuid[])) AND b.building_code IS NOT NULL)")
    return " AND (" + " OR ".join(parts) + ")", {"mw_bids": ids}


def _done_sql(alias: str = "w") -> str:
    states = ", ".join(f"'{s}'" for s in DONE_STATES)
    return (f"lower(coalesce({alias}.status, '')) NOT IN ({states})"
            f" AND {alias}.completed_at IS NULL AND {alias}.closed_at IS NULL")


async def _pending_keys(session: AsyncSession, org: UUID, item_type: str) -> dict[str, dict[str, Any]]:
    rows = (await session.execute(text("""
        SELECT id, payload FROM plenum_cafm.approvals_queue_items
         WHERE organization_id = :o AND source_feature = :s AND item_type = :t AND status = 'pending'
           AND payload->>'raised_by' = :rb"""),
        {"o": org, "s": SOURCE, "t": item_type, "rb": RAISED_BY})).mappings().all()
    out = {}
    for r in rows:
        p = r["payload"] or {}
        if p.get("dedupe_key"):
            out[str(p["dedupe_key"])] = {"id": r["id"], "building_id": p.get("building_id")}
    return out


async def raise_and_clear(session: AsyncSession, *, org: UUID, item_type: str, findings: list[dict[str, Any]],
                          building_ids: tuple[UUID, ...] | None, job_label: str,
                          clear_stale: bool = True) -> dict[str, int]:
    """Raise one pending approval per new finding; clear the pending ones no longer found.

    Each finding: {key, summary, severity, payload, entity_type?, entity_id?, building_id?}."""
    from ...shared.approvals import enqueue_approval

    pending = await _pending_keys(session, org, item_type)
    raised = 0
    for f in findings:
        if f["key"] in pending:
            continue
        await enqueue_approval(
            session, source_feature=SOURCE, item_type=item_type, summary=f["summary"][:500],
            severity=f.get("severity") or "medium", organization_id=org,
            related_entity_type=f.get("entity_type"), related_entity_id=f.get("entity_id"),
            payload={**(f.get("payload") or {}), "dedupe_key": f["key"], "raised_by": RAISED_BY,
                     "building_id": f.get("building_id"), "job": job_label})
        raised += 1
    cleared = 0
    if clear_stale:
        found = {f["key"] for f in findings}
        scope = {str(b) for b in building_ids} if building_ids is not None else None
        stale = [v["id"] for k, v in pending.items()
                 if k not in found and (scope is None or (v["building_id"] and str(v["building_id"]) in scope))]
        if stale:
            note = f"Cleared automatically: the {job_label} run on {_now():%d %b %Y %H:%M} UTC no longer finds it."
            res = await session.execute(text("""
                UPDATE plenum_cafm.approvals_queue_items
                   SET status = 'dismissed', pm_notes = :n, decided_at = now(), updated_at = now()
                 WHERE id = ANY(:ids) AND status = 'pending'"""), {"n": note, "ids": stale})
            cleared = res.rowcount or 0
    await session.commit()
    return {"approvals_raised": raised, "approvals_cleared": cleared}


def _utc(v: datetime) -> datetime:
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v


def _hours(td: timedelta) -> int:
    return int(td.total_seconds() // 3600)


def _when(d: Any) -> str:
    if isinstance(d, datetime):
        return d.strftime("%d %b %Y %H:%M")
    if isinstance(d, date):
        return d.strftime("%d %b %Y")
    return str(d or "—")


# ── 1. work-order SLA watch ───────────────────────────────────────────────────────

async def sla_watch(session: AsyncSession, *, organization_id: UUID, building_ids: tuple[UUID, ...] | None = None,
                    horizon_hours: int = 24, raise_approvals: bool = True) -> dict[str, Any]:
    """Open work orders past their SLA, and those due inside the horizon. One approval per breach."""
    now = _now()
    hours = max(1, int(horizon_hours))
    pred, params = _scope_sql(building_ids, building_col="w.building_id")
    rows = (await session.execute(text(f"""
        SELECT w.id, w.wo_code, coalesce(w.title, w.issue_description, w.task_description, '') AS title,
               w.status, w.priority, w.sla_due_at, w.building_id, w.building_code,
               coalesce(w.vendor_name, w.vendor) AS vendor, coalesce(w.asset_name, w.asset, w.asset_code) AS asset
          FROM plenum_cafm.work_orders w
         WHERE w.organization_id = :o AND w.sla_due_at IS NOT NULL AND {_done_sql()}
           AND w.sla_due_at < now() + make_interval(hours => :h){pred}
         ORDER BY w.sla_due_at"""),
        {"o": organization_id, "h": hours, **params})).mappings().all()
    # sla_due_at is a timestamp with a zone on one database and without on another; naive is UTC.
    rows = [{**dict(r), "sla_due_at": _utc(r["sla_due_at"])} for r in rows]
    open_n = (await session.execute(text(f"""
        SELECT count(*) FROM plenum_cafm.work_orders w WHERE w.organization_id = :o AND {_done_sql()}{pred}"""),
        {"o": organization_id, **params})).scalar_one()
    breached = [r for r in rows if r["sla_due_at"] <= now]
    soon = [r for r in rows if r["sla_due_at"] > now]
    findings = []
    for r in breached:
        late = _hours(now - r["sla_due_at"])
        hi = str(r["priority"] or "").lower() in ("highest", "urgent", "critical", "p1", "emergency")
        findings.append({
            "key": f"wo_sla:{r['id']}", "entity_type": "work_order", "entity_id": r["id"],
            "building_id": str(r["building_id"]) if r["building_id"] else None,
            "severity": "critical" if hi or late >= 72 else "high" if late >= 24 else "medium",
            "summary": f"Work order {r['wo_code'] or ''} past its SLA by {late} h — {r['title'][:90]}"
                       f"{' · ' + r['vendor'] if r['vendor'] else ''}".strip(),
            "payload": {"wo_code": r["wo_code"], "status": r["status"], "priority": r["priority"],
                        "sla_due_at": r["sla_due_at"].isoformat(), "hours_late": late, "vendor": r["vendor"],
                        "asset": r["asset"], "building_code": r["building_code"],
                        "action": "Chase the vendor or re-assign; approve once it is in hand."}})
    out: dict[str, Any] = {"ok": True, "open_work_orders": int(open_n), "past_sla": len(breached),
                           f"due_in_{horizon_hours}h": len(soon)}
    if raise_approvals:
        out.update(await raise_and_clear(session, org=organization_id, item_type="wo_sla_breach", findings=findings,
                                         building_ids=building_ids, job_label="Work-order SLA watch"))
    out["report_lines"] = (
        [f"Past SLA — {r['wo_code'] or '?'} · {r['status']} · {r['priority'] or 'no priority'} · "
         f"{_hours(now - r['sla_due_at'])} h late · {r['vendor'] or 'no vendor'} · {r['building_code'] or ''}".rstrip(" ·")
         for r in breached[:REPORT_LINES]]
        + [f"Due soon — {r['wo_code'] or '?'} · due {_when(r['sla_due_at'])} · {r['vendor'] or 'no vendor'}"
           for r in soon[:REPORT_LINES]])
    return out


# ── 2. PPM due list ───────────────────────────────────────────────────────────────

async def ppm_due(session: AsyncSession, *, organization_id: UUID, building_ids: tuple[UUID, ...] | None = None,
                  days: int = 14, raise_approvals: bool = True) -> dict[str, Any]:
    """Maintenance plans overdue or due within `days`. One approval per overdue plan with no
    open work order raised for it."""
    today = date.today()
    pred, params = _scope_sql(building_ids, asset_col="p.asset_id", code_col="p.building_code")
    rows = (await session.execute(text(f"""
        SELECT p.id, p.sm_code, coalesce(p.description, p.sm_code, '') AS description, p.next_due_date,
               p.frequency_type, p.frequency_value, p.building_code, p.vendor_name, p.asset_code,
               (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.building_code = p.building_code
                 AND b.organization_id = p.organization_id LIMIT 1) AS building_id,
               EXISTS (SELECT 1 FROM plenum_cafm.work_orders w WHERE w.maintenance_plan_id = p.id
                        AND {_done_sql()}) AS has_open_wo
          FROM plenum_cafm.maintenance_plans p
         WHERE p.organization_id = :o AND p.next_due_date IS NOT NULL AND p.next_due_date <= :until
           AND lower(coalesce(p.status, 'active')) NOT IN ('inactive', 'retired', 'cancelled', 'archived'){pred}
         ORDER BY p.next_due_date"""),
        {"o": organization_id, "until": today + timedelta(days=days), **params})).mappings().all()
    overdue = [r for r in rows if r["next_due_date"] < today]
    due = [r for r in rows if r["next_due_date"] >= today]
    findings = [{
        "key": f"ppm_due:{r['id']}:{r['next_due_date']}", "entity_type": "maintenance_plan", "entity_id": r["id"],
        "building_id": str(r["building_id"]) if r["building_id"] else None,
        "severity": "high" if (today - r["next_due_date"]).days > 7 else "medium",
        "summary": f"PPM overdue — {r['description'][:80]} ({r['asset_code'] or r['sm_code'] or 'no asset'}), "
                   f"due {_when(r['next_due_date'])}{' · ' + r['vendor_name'] if r['vendor_name'] else ''}",
        "payload": {"sm_code": r["sm_code"], "asset_code": r["asset_code"], "next_due_date": r["next_due_date"].isoformat(),
                    "days_overdue": (today - r["next_due_date"]).days, "vendor": r["vendor_name"],
                    "building_code": r["building_code"],
                    "action": "Raise the PPM work order with the vendor; approve once it is raised."},
    } for r in overdue if not r["has_open_wo"]]
    out: dict[str, Any] = {"ok": True, "plans_overdue": len(overdue), f"plans_due_{days}d": len(due),
                           "already_have_open_wo": sum(1 for r in rows if r["has_open_wo"])}
    if raise_approvals:
        out.update(await raise_and_clear(session, org=organization_id, item_type="ppm_overdue", findings=findings,
                                         building_ids=building_ids, job_label="PPM due list"))
    out["report_lines"] = (
        [f"Overdue — {r['description'][:60]} · {r['asset_code'] or ''} · due {_when(r['next_due_date'])} · "
         f"{r['vendor_name'] or 'no vendor'}" for r in overdue[:REPORT_LINES]]
        + [f"Due {_when(r['next_due_date'])} — {r['description'][:60]} · {r['asset_code'] or ''} · "
           f"{r['vendor_name'] or 'no vendor'} · {r['building_code'] or ''}".rstrip(" ·") for r in due[:REPORT_LINES]])
    return out


# ── 3. missed PPM follow-up ───────────────────────────────────────────────────────

async def ppm_missed(session: AsyncSession, *, organization_id: UUID, building_ids: tuple[UUID, ...] | None = None,
                     raise_approvals: bool = True) -> dict[str, Any]:
    """PPM visits Missed, or Deferred past their new date, with no later visit of the same PPM
    on the same asset done or booked. One approval per visit to rebook."""
    today = date.today()
    pred, params = _scope_sql(building_ids, asset_col="v.asset_id", code_col="v.building_code")
    rows = (await session.execute(text(f"""
        SELECT v.id, v.ppm_ref, coalesce(v.task, v.ppm_ref, '') AS task, v.asset_code, v.status, v.scheduled_date,
               v.deferred_to, v.deferral_reason, v.vendor_name, v.building_code,
               (SELECT b.building_id FROM plenum_cafm.buildings b WHERE b.building_code = v.building_code
                 AND b.organization_id = v.organization_id LIMIT 1) AS building_id
          FROM plenum_cafm.ppm_visits v
         WHERE v.organization_id = :o
           AND (lower(coalesce(v.status, '')) = 'missed'
                OR (lower(coalesce(v.status, '')) = 'deferred' AND coalesce(v.deferred_to, v.scheduled_date) < :today))
           AND NOT EXISTS (
                SELECT 1 FROM plenum_cafm.ppm_visits n
                 WHERE n.organization_id = v.organization_id AND n.id <> v.id
                   AND coalesce(n.asset_code, '') = coalesce(v.asset_code, '')
                   AND coalesce(n.ppm_ref, n.task, '') = coalesce(v.ppm_ref, v.task, '')
                   AND n.scheduled_date > v.scheduled_date
                   AND lower(coalesce(n.status, '')) IN ('completed', 'complete', 'scheduled', 'booked'))
           {pred}
         ORDER BY v.scheduled_date"""), {"o": organization_id, "today": today, **params})).mappings().all()
    findings = [{
        "key": f"ppm_missed:{r['id']}", "entity_type": "ppm_visit", "entity_id": r["id"],
        "building_id": str(r["building_id"]) if r["building_id"] else None,
        "severity": "high" if (today - (r["scheduled_date"] or today)).days > 30 else "medium",
        "summary": f"Rebook {r['status'].lower()} PPM — {r['task'][:80]} ({r['asset_code'] or 'no asset'}), "
                   f"was due {_when(r['scheduled_date'])}{' · ' + r['vendor_name'] if r['vendor_name'] else ''}",
        "payload": {"ppm_ref": r["ppm_ref"], "asset_code": r["asset_code"], "status": r["status"],
                    "scheduled_date": r["scheduled_date"].isoformat() if r["scheduled_date"] else None,
                    "deferred_to": r["deferred_to"].isoformat() if r["deferred_to"] else None,
                    "deferral_reason": r["deferral_reason"], "vendor": r["vendor_name"],
                    "building_code": r["building_code"],
                    "action": "Rebook the visit with the vendor; approve once it has a date."},
    } for r in rows]
    by_vendor: dict[str, int] = {}
    for r in rows:
        by_vendor[r["vendor_name"] or "no vendor"] = by_vendor.get(r["vendor_name"] or "no vendor", 0) + 1
    out: dict[str, Any] = {"ok": True, "visits_to_rebook": len(rows),
                           "missed": sum(1 for r in rows if str(r["status"]).lower() == "missed"),
                           "deferred_past_date": sum(1 for r in rows if str(r["status"]).lower() == "deferred"),
                           "vendors": len(by_vendor)}
    if raise_approvals:
        out.update(await raise_and_clear(session, org=organization_id, item_type="ppm_missed", findings=findings,
                                         building_ids=building_ids, job_label="Missed PPM follow-up"))
    out["report_lines"] = (
        [f"{v} — {n} visit{'s' if n != 1 else ''} to rebook" for v, n in sorted(by_vendor.items(), key=lambda x: -x[1])]
        + [f"{r['status']} — {r['task'][:60]} · {r['asset_code'] or ''} · was due {_when(r['scheduled_date'])}"
           for r in rows[:REPORT_LINES]])
    return out


# ── 4. spare parts reorder ────────────────────────────────────────────────────────

async def parts_reorder(session: AsyncSession, *, organization_id: UUID, raise_approvals: bool = True) -> dict[str, Any]:
    """Parts at or below their reorder level. Stock is the company's, not a building's."""
    rows = (await session.execute(text("""
        SELECT id, part_code, coalesce(part_name, part_code, '') AS part_name, stock_quantity, reorder_level,
               max_quantity, unit_price, coalesce(supplier, vendor_name) AS supplier
          FROM plenum_cafm.spare_parts
         WHERE organization_id = :o AND reorder_level IS NOT NULL AND coalesce(stock_quantity, 0) <= reorder_level
         ORDER BY coalesce(stock_quantity, 0), part_name"""), {"o": organization_id})).mappings().all()
    total = (await session.execute(text("SELECT count(*) FROM plenum_cafm.spare_parts WHERE organization_id = :o"),
                                   {"o": organization_id})).scalar_one()

    def order_qty(r) -> int:
        stock = int(r["stock_quantity"] or 0)
        target = int(r["max_quantity"] or 0) or int(r["reorder_level"] or 0) * 2 or 1
        return max(target - stock, 1)

    findings = [{
        "key": f"part:{r['id']}", "entity_type": "spare_part", "entity_id": r["id"],
        "severity": "critical" if not r["stock_quantity"] else "high" if r["stock_quantity"] < (r["reorder_level"] or 0) else "medium",
        "summary": f"Reorder {r['part_name'][:70]} ({r['part_code'] or '—'}): {int(r['stock_quantity'] or 0)} in stock, "
                   f"reorder level {r['reorder_level']} — order {order_qty(r)}"
                   f"{' from ' + r['supplier'] if r['supplier'] else ''}",
        "payload": {"part_code": r["part_code"], "stock": int(r["stock_quantity"] or 0), "reorder_level": r["reorder_level"],
                    "order_quantity": order_qty(r), "supplier": r["supplier"],
                    "estimated_cost": float(r["unit_price"] or 0) * order_qty(r) if r["unit_price"] else None,
                    "action": "Raise the purchase order; approve once it is placed."},
    } for r in rows]
    out: dict[str, Any] = {"ok": True, "parts_checked": int(total), "parts_to_reorder": len(rows),
                           "out_of_stock": sum(1 for r in rows if not r["stock_quantity"])}
    if raise_approvals:
        out.update(await raise_and_clear(session, org=organization_id, item_type="part_reorder", findings=findings,
                                         building_ids=None, job_label="Spare parts reorder"))
    out["report_lines"] = [f"{r['part_name'][:60]} ({r['part_code'] or '—'}) — {int(r['stock_quantity'] or 0)} in stock, "
                           f"level {r['reorder_level']}, order {order_qty(r)} · {r['supplier'] or 'no supplier'}"
                           for r in rows[:REPORT_LINES]]
    return out


# ── 5. monthly maintenance summary ────────────────────────────────────────────────

def previous_month(today: date | None = None) -> date:
    today = today or date.today()
    return (date(today.year, today.month, 1) - timedelta(days=1)).replace(day=1)


def _pct(a: int, b: int) -> float | None:
    return round(100.0 * a / b, 1) if b else None


async def monthly_summary(session: AsyncSession, *, organization_id: UUID, building_ids: tuple[UUID, ...] | None = None,
                          month: date | None = None, raise_approvals: bool = True) -> dict[str, Any]:
    """Last month's maintenance: work orders raised and closed, SLA hit rate, PPM completion,
    by building and by vendor. A vendor below target (SLA 80 %, PPM 90 %, at least 3 jobs) is
    raised for review; the month's items are not cleared by later months."""
    m0 = (month or previous_month()).replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1)
    wpred, wparams = _scope_sql(building_ids, building_col="w.building_id")
    vpred, vparams = _scope_sql(building_ids, asset_col="v.asset_id", code_col="v.building_code")
    wo = (await session.execute(text(f"""
        SELECT coalesce(w.vendor_name, w.vendor, 'no vendor') AS vendor, coalesce(w.building_code, '—') AS building,
               count(*) FILTER (WHERE coalesce(w.raised_at, w.reported_at, w.created_at) >= CAST(:a AS date)
                                  AND coalesce(w.raised_at, w.reported_at, w.created_at) < CAST(:b AS date)) AS raised,
               count(*) FILTER (WHERE coalesce(w.closed_at, w.completed_at) >= CAST(:a AS date)
                                  AND coalesce(w.closed_at, w.completed_at) < CAST(:b AS date)) AS closed,
               count(*) FILTER (WHERE coalesce(w.closed_at, w.completed_at) >= CAST(:a AS date)
                                  AND coalesce(w.closed_at, w.completed_at) < CAST(:b AS date) AND w.sla_due_at IS NOT NULL) AS with_sla,
               count(*) FILTER (WHERE coalesce(w.closed_at, w.completed_at) >= CAST(:a AS date)
                                  AND coalesce(w.closed_at, w.completed_at) < CAST(:b AS date) AND w.sla_due_at IS NOT NULL
                                  AND coalesce(w.closed_at, w.completed_at) <= w.sla_due_at) AS in_sla
          FROM plenum_cafm.work_orders w WHERE w.organization_id = :o{wpred}
         GROUP BY 1, 2"""), {"o": organization_id, "a": m0, "b": m1, **wparams})).mappings().all()
    ppm = (await session.execute(text(f"""
        SELECT coalesce(v.vendor_name, 'no vendor') AS vendor, coalesce(v.building_code, '—') AS building,
               count(*) AS scheduled,
               count(*) FILTER (WHERE lower(coalesce(v.status, '')) IN ('completed', 'complete')) AS completed,
               count(*) FILTER (WHERE lower(coalesce(v.status, '')) = 'missed') AS missed,
               count(*) FILTER (WHERE v.within_tolerance IS TRUE) AS on_time
          FROM plenum_cafm.ppm_visits v
         WHERE v.organization_id = :o AND v.scheduled_date >= :a AND v.scheduled_date < :b{vpred}
         GROUP BY 1, 2"""), {"o": organization_id, "a": m0, "b": m1, **vparams})).mappings().all()

    def roll(rows, key, fields):
        acc: dict[str, dict[str, int]] = {}
        for r in rows:
            a = acc.setdefault(r[key], {f: 0 for f in fields})
            for f in fields:
                a[f] += int(r[f] or 0)
        return acc

    WF, PF = ("raised", "closed", "with_sla", "in_sla"), ("scheduled", "completed", "missed", "on_time")
    w_tot = {f: sum(int(r[f] or 0) for r in wo) for f in WF}
    p_tot = {f: sum(int(r[f] or 0) for r in ppm) for f in PF}
    w_vendor, p_vendor = roll(wo, "vendor", WF), roll(ppm, "vendor", PF)
    w_bld, p_bld = roll(wo, "building", WF), roll(ppm, "building", PF)

    findings = []
    for v, a in w_vendor.items():
        hit = _pct(a["in_sla"], a["with_sla"])
        if v != "no vendor" and a["with_sla"] >= 3 and hit is not None and hit < SLA_TARGET_PCT:
            findings.append({"key": f"kpi_sla:{m0}:{v}", "severity": "high",
                             "summary": f"{v}: {hit}% of work orders closed within SLA in {m0:%B %Y} "
                                        f"({a['in_sla']} of {a['with_sla']}) — target {SLA_TARGET_PCT:.0f}%",
                             "payload": {"vendor": v, "month": m0.isoformat(), "sla_hit_pct": hit, **a,
                                         "action": "Review with the vendor at the monthly meeting."}})
    for v, a in p_vendor.items():
        done = _pct(a["completed"], a["scheduled"])
        if v != "no vendor" and a["scheduled"] >= 3 and done is not None and done < PPM_TARGET_PCT:
            findings.append({"key": f"kpi_ppm:{m0}:{v}", "severity": "high",
                             "summary": f"{v}: {done}% of PPM visits completed in {m0:%B %Y} "
                                        f"({a['completed']} of {a['scheduled']}, {a['missed']} missed) — target {PPM_TARGET_PCT:.0f}%",
                             "payload": {"vendor": v, "month": m0.isoformat(), "ppm_completion_pct": done, **a,
                                         "action": "Review with the vendor at the monthly meeting."}})
    if building_ids is not None and len(building_ids) == 1:
        for f in findings:
            f["building_id"] = str(building_ids[0])

    out: dict[str, Any] = {
        "ok": True, "month": m0.strftime("%B %Y"),
        "work_orders_raised": w_tot["raised"], "work_orders_closed": w_tot["closed"],
        "sla_hit_pct": _pct(w_tot["in_sla"], w_tot["with_sla"]),
        "ppm_scheduled": p_tot["scheduled"], "ppm_completed": p_tot["completed"],
        "ppm_completion_pct": _pct(p_tot["completed"], p_tot["scheduled"]), "ppm_missed": p_tot["missed"],
        "vendors_below_target": len(findings),
    }
    if raise_approvals:
        out.update(await raise_and_clear(session, org=organization_id, item_type="maintenance_kpi", findings=findings,
                                         building_ids=building_ids, job_label="Monthly maintenance summary",
                                         clear_stale=False))

    def lines(title, wacc, pacc):
        names = sorted(set(wacc) | set(pacc), key=lambda n: -(wacc.get(n, {}).get("closed", 0) + pacc.get(n, {}).get("scheduled", 0)))
        out_l = [title]
        for n in names[:REPORT_LINES]:
            a, p = wacc.get(n, {}), pacc.get(n, {})
            sla = _pct(a.get("in_sla", 0), a.get("with_sla", 0))
            done = _pct(p.get("completed", 0), p.get("scheduled", 0))
            line = f"  {n} — {a.get('raised', 0)} raised, {a.get('closed', 0)} closed"
            if sla is not None:
                line += f", {sla}% in SLA"
            if done is not None:
                line += f" · PPM {done}% ({p.get('completed', 0)}/{p.get('scheduled', 0)})"
            out_l.append(line)
        return out_l

    out["report_lines"] = lines("By building", w_bld, p_bld) + lines("By vendor", w_vendor, p_vendor)
    out["details"] = {"by_vendor": {"work_orders": w_vendor, "ppm": p_vendor},
                      "by_building": {"work_orders": w_bld, "ppm": p_bld}}
    return out
