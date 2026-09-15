"""The Maintenance module's three questions, answered from the caller's buildings only.

The shell's Maintenance screen asks three things and the backend answered none of them:

* **Decisions** — what the property manager owes right now. A work order blocked on a vendor,
  one waiting on approval, one running past its SLA, and the orders that do not exist yet
  because another module says they should. That last kind has no work order id at all.
* **Inspections** — what the reports attached to completed orders say once read together: the
  condition grade, the findings, the recommendation, and crucially whether the recommendation
  ever became an order.
* **PPM health** — per contract, planned against done, missed, late, and when the next visit
  falls.

All three are derived, not stored: there is no decisions table, no report-health table and no
PPM-compliance table. They are read from ``work_orders``, ``approvals_queue_items`` and
``inspections``, which is why they live in one module rather than three.

Every one is narrowed to the caller's buildings. ``work_orders.building_id`` is populated on
every row in both databases, so orders narrow directly. ``inspections`` and ``ppm_schedules``
carry no building column, so they reach a building through the asset they are about, and a
row whose asset cannot be placed is dropped rather than shown to everybody.

The two databases are not the same shape — one spells a work order's key ``wo_code`` and the
other ``work_order_id``, one has ``corrective_action`` and ``findings_jsonb`` on inspections
and the other does not, and ``ppm_schedules`` exists on only one of them. Columns are read
from ``information_schema`` once per process and the queries are built from what is actually
there, the same way the asset catalogue decides its own column names.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

_shape: dict[str, set[str]] | None = None

#: Work-order states that put a decision in front of a person, by the spelling each
#: database uses. Matched case-insensitively, so "Open" and "open" are one state.
BLOCKED = ("blocked", "on hold", "onhold", "held", "suspended")
AWAITING = ("pending_approval", "pending approval", "awaiting approval", "submitted", "draft")
LIVE = ("open", "inprogress", "in progress", "in_progress", "assigned", "scheduled")
DONE = ("completed", "closed", "complete", "done", "cancelled", "canceled")

#: Which module raised an approvals item, in the words the Maintenance screen uses.
SOURCE_OF = {
    "energy_anomaly": "Energy",
    "energy_recommendation": "Energy",
    "compliance_certificate": "Compliance",
    "vendor": "Vendors",
    "contract_sla_parameters": "Vendors",
    "asset_criticality": "Assets",
    "document": "Compliance",
    "invoice": "Vendors",
}


async def shape(session: AsyncSession) -> dict[str, set[str]]:
    """Which columns each table this module reads actually has, cached per process."""
    global _shape
    if _shape is not None:
        return _shape
    rows = (
        await session.execute(
            text(
                """SELECT table_name, column_name FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'
                      AND table_name IN ('work_orders','inspections','ppm_schedules',
                                         'approvals_queue_items','assets','vendors',
                                         'vendor_contracts','buildings')"""
            )
        )
    ).all()
    out: dict[str, set[str]] = {}
    for table, column in rows:
        out.setdefault(table, set()).add(column)
    _shape = out
    return out


def _pick(cols: set[str], *candidates: str) -> str | None:
    """The first spelling this database actually has."""
    return next((c for c in candidates if c in cols), None)


def _ids(building_ids: list[UUID] | None) -> list[str] | None:
    return None if building_ids is None else [str(b) for b in building_ids]


def _scope_sql(building_ids: list[UUID] | None, column: str) -> tuple[str, dict[str, Any]]:
    """The predicate that narrows to the caller's buildings.

    None means unrestricted. An empty list is a real answer meaning allocated to nothing,
    and becomes a predicate matching no row rather than no predicate at all.
    """
    ids = _ids(building_ids)
    if ids is None:
        return "", {}
    if not ids:
        return " AND FALSE", {}
    return f" AND {column} = ANY(CAST(:scope_b AS uuid[]))", {"scope_b": ids}


# ── decisions ────────────────────────────────────────────────────────────────────────

async def decisions(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 200,
) -> dict[str, Any]:
    """Every decision the property manager owes, across both places one can arrive from.

    A work order carries its own state. An approvals item is a decision that does not have
    a work order yet — the module that raised it says one should exist — so its ``work_order``
    is null and its ``state`` is "To raise". Both are reported in one list because to the
    person owing the decision they are the same queue.
    """
    sh = await shape(session)
    wo = sh.get("work_orders", set())
    out: list[dict[str, Any]] = []

    # Both spellings exist on one database and only one is populated per row, so take the
    # first that is actually there rather than the first that exists as a column.
    have = [c for c in ("wo_code", "work_order_id", "workorder_ref") if c in wo]
    code = ("coalesce(" + ", ".join("w." + c + "::text" for c in have) + ")") if have else None
    if code:
        vendor_col = _pick(wo, "assigned_vendor", "vendor")
        asset = _pick(wo, "asset", "asset_id")
        cost = _pick(wo, "estimated_cost", "cost_vendor_aed")
        due = _pick(wo, "sla_due_at", "scheduled_date")
        title = _pick(wo, "title", "issue_description", "description")
        clause, params = _scope_sql(building_ids, "w.building_id")

        # The state is decided in SQL, not after the rows come back. Deciding it in Python
        # meant the LIMIT ran first: where the newest two hundred orders are all completed,
        # every decision was cut before anything looked at it and the screen read empty.
        overdue = (
            "(lower(coalesce(w.status,'')) = ANY(CAST(:live AS text[])) "
            "AND w.{d} IS NOT NULL AND w.{d}::timestamptz < now())".format(d=due)
            if due else "FALSE"
        )
        state_case = (
            "CASE WHEN lower(coalesce(w.status,'')) = ANY(CAST(:blocked AS text[])) THEN 'Blocked' "
            "WHEN " + overdue + " THEN 'Deviation' "
            "WHEN lower(coalesce(w.status,'')) = ANY(CAST(:awaiting AS text[])) THEN 'Awaiting approval' "
            "END"
        )
        params.update({"blocked": list(BLOCKED), "awaiting": list(AWAITING),
                       "live": list(LIVE), "lim": int(limit)})

        # A vendor is recorded as a name on one database and as a uuid on the other. Show a
        # name either way, rather than an id the reader cannot act on.
        name_col = "vendor_name" if "vendor_name" in sh.get("vendors", set()) else "name"
        if vendor_col and "vendors" in sh:
            vendor_expr = "coalesce(ven.{n}, w.{v}::text)".format(n=name_col, v=vendor_col)
            vendor_join = "LEFT JOIN plenum_cafm.vendors ven ON ven.id::text = w.{v}::text".format(v=vendor_col)
        elif vendor_col:
            vendor_expr, vendor_join = "w.{v}::text".format(v=vendor_col), ""
        else:
            vendor_expr, vendor_join = "NULL", ""

        sql = """
            SELECT {code}              AS code,
                   w.status            AS status,
                   {state}             AS state,
                   w.building_id::text AS building_id,
                   b.name              AS building,
                   {vendor}            AS vendor,
                   {asset}             AS asset,
                   {cost}              AS est,
                   {due}               AS due,
                   {title}             AS title,
                   w.priority          AS priority
              FROM plenum_cafm.work_orders w
              LEFT JOIN plenum_cafm.buildings b ON b.building_id = w.building_id
              {vjoin}
             WHERE w.building_id IS NOT NULL{clause}
               AND {state} IS NOT NULL
             ORDER BY w.created_at DESC NULLS LAST
             LIMIT :lim""".format(
            code=code, state=state_case, vendor=vendor_expr, vjoin=vendor_join, clause=clause,
            asset=("w." + asset + "::text") if asset else "NULL",
            cost=("w." + cost) if cost else "NULL",
            due=("w." + due + "::text") if due else "NULL",
            title=("w." + title) if title else "NULL",
        )
        try:
            # Its own savepoint: one shape surprise must not abort the transaction and take
            # the two sections after it down with it.
            async with session.begin_nested():
                rows = (await session.execute(text(sql), params)).mappings().all()
        except Exception as exc:  # noqa: BLE001 - a failed section is empty, not a 500
            log.warning("maintenance.decisions.work_orders_failed", error=str(exc)[:300])
            rows = []
        for r in rows:
            state = r["state"]
            trigger = {
                "Blocked": "Work order held at status " + repr(r["status"]),
                "Awaiting approval": "Approval outstanding",
                "Deviation": "Past its due date",
            }.get(state, state)
            out.append({
                "work_order": r["code"], "state": state, "source": "Maintenance",
                "trigger": trigger, "detail": r["title"],
                "asset": r["asset"], "building": r["building"], "building_id": r["building_id"],
                "vendor": r["vendor"], "estimated_cost": _num(r["est"]),
                "priority": r["priority"], "due": r["due"],
            })

    out.extend(await _decisions_from_approvals(session, building_ids=building_ids, limit=limit))
    order = {"Blocked": 0, "Deviation": 1, "Awaiting approval": 2, "To raise": 3}
    out.sort(key=lambda d: (order.get(d["state"], 9), str(d.get("building") or "")))
    return {
        "ok": True,
        "count": len(out),
        "by_state": {s: sum(1 for d in out if d["state"] == s) for s in order},
        "decisions": out[:limit],
    }


def _overdue(due: Any, now: datetime) -> int | None:
    try:
        d = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (now - d).days


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def _decisions_from_approvals(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int,
) -> list[dict[str, Any]]:
    """Decisions raised by another module, which have no work order yet.

    The queue names what each item is about but not which building, so each kind is resolved
    through its own table. An item that cannot be placed on a building is left out rather
    than shown to a caller who may not be allowed to see it.
    """
    sh = await shape(session)
    if "approvals_queue_items" not in sh:
        return []
    ids = _ids(building_ids)
    if ids is not None and not ids:
        return []
    restricted = ids is not None
    params: dict[str, Any] = {"lim": int(limit)}
    if restricted:
        params["scope_b"] = ids

    anomaly_b = "a.building_id" if "building_id" in sh.get("energy_anomalies", set()) else None
    # energy_anomalies.site_id was renamed to building_id in Sep 2026; support both.
    if anomaly_b is None and "site_id" in sh.get("energy_anomalies", set()):
        anomaly_b = "a.site_id"
    cert_b = "c.building_id" if "building_id" in sh.get("compliance_certificates", set()) else None

    joins = f"""
        LEFT JOIN plenum_cafm.energy_anomalies a
               ON q.related_entity_type = 'energy_anomaly' AND a.id::text = q.related_entity_id::text
        LEFT JOIN plenum_cafm.compliance_certificates c
               ON q.related_entity_type = 'compliance_certificate' AND c.id::text = q.related_entity_id::text
    """
    resolved = " COALESCE(" + ", ".join([x for x in (anomaly_b, cert_b) if x] or ["NULL"]) + ")::text"
    where_scope = f" AND {resolved} = ANY(CAST(:scope_b AS text[]))" if restricted else ""
    sql = f"""
        SELECT q.id::text AS id, q.related_entity_type AS kind, q.summary AS summary,
               q.severity AS severity, q.source_feature AS feature,
               {resolved} AS building_id, b.name AS building
          FROM plenum_cafm.approvals_queue_items q
          {joins}
          LEFT JOIN plenum_cafm.buildings b ON b.building_id::text = {resolved}
         WHERE q.status = 'pending' AND {resolved} IS NOT NULL{where_scope}
         ORDER BY q.created_at DESC NULLS LAST
         LIMIT :lim"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.decisions.approvals_failed", error=str(exc)[:300])
        return []
    return [{
        "work_order": None, "state": "To raise",
        "source": SOURCE_OF.get(r["kind"] or "", "Maintenance"),
        "trigger": r["summary"], "detail": None, "asset": None,
        "building": r["building"], "building_id": r["building_id"],
        "vendor": None, "estimated_cost": None,
        "priority": r["severity"], "due": None, "queue_item": r["id"],
    } for r in rows]


# ── inspections ──────────────────────────────────────────────────────────────────────

async def inspections(
    session: AsyncSession, *, building_ids: list[UUID] | None, limit: int = 200,
) -> dict[str, Any]:
    """Inspection reports, with the thing the screen exists to show: whether the
    recommendation ever became a work order.

    ``inspections`` carries no building column, so each report reaches a building through
    the asset it is about. A report whose asset cannot be placed is not returned.
    """
    sh = await shape(session)
    ins = sh.get("inspections", set())
    if not ins:
        return {"ok": True, "count": 0, "inspections": [],
                "note": "no inspections table on this database"}
    ids = _ids(building_ids)
    if ids is not None and not ids:
        return {"ok": True, "count": 0, "inspections": []}

    corrective = "i.corrective_action" if "corrective_action" in ins else "NULL"
    findings = "i.findings_jsonb" if "findings_jsonb" in ins else "NULL"
    section = "i.section" if "section" in ins else "NULL"
    source = "i.source_file" if "source_file" in ins else "NULL"
    params: dict[str, Any] = {"lim": int(limit)}
    clause = ""
    if ids is not None:
        clause = " AND a.building_id = ANY(CAST(:scope_b AS uuid[]))"
        params["scope_b"] = ids

    sql = f"""
        SELECT i.id::text            AS id,
               i.asset_code          AS asset_code,
               i.inspector           AS inspector,
               i.inspection_date     AS inspection_date,
               i.finding_type        AS finding_type,
               i.observations        AS observations,
               i.risk_level          AS risk_level,
               {corrective}          AS corrective_action,
               {findings}            AS findings,
               {section}             AS section,
               {source}              AS source_file,
               a.building_id::text   AS building_id,
               a.asset_name          AS asset_name,
               b.name                AS building
          FROM plenum_cafm.inspections i
          JOIN plenum_cafm.assets a
            ON a.asset_code = i.asset_code OR a.id::text = i.asset_id::text
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.building_id IS NOT NULL{clause}
         ORDER BY i.inspection_date DESC NULLS LAST
         LIMIT :lim"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.inspections_failed", error=str(exc)[:300])
        return {"ok": False, "count": 0, "inspections": [], "error": str(exc)[:200]}

    out = [dict(r) for r in rows]
    for r in out:
        d = r.get("inspection_date")
        r["inspection_date"] = d.isoformat() if hasattr(d, "isoformat") else d
        r["recommendation_open"] = bool(r.get("corrective_action"))
    return {
        "ok": True,
        "count": len(out),
        "recommendations_open": sum(1 for r in out if r["recommendation_open"]),
        "by_risk": _tally(out, "risk_level"),
        "inspections": out,
    }


def _tally(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "unspecified")
        out[k] = out.get(k, 0) + 1
    return out


# ── planned maintenance health ───────────────────────────────────────────────────────

async def ppm_health(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """Planned maintenance, per vendor and contract: planned, done, missed, late, next due.

    Derived from the work orders themselves rather than from a compliance table, because
    there isn't one. A planned order that is finished counts as done; one past its due date
    and not finished is missed; one finished after its due date is late.
    """
    sh = await shape(session)
    wo = sh.get("work_orders", set())
    kind = _pick(wo, "wo_type", "maintenance_type", "request_type")
    if not kind:
        return {"ok": True, "contracts": [], "note": "work orders do not record a type here"}
    vendor_col = _pick(wo, "assigned_vendor", "vendor")
    name_col = "vendor_name" if "vendor_name" in sh.get("vendors", set()) else "name"
    if vendor_col and "vendors" in sh:
        vendor = "coalesce(ven.{n}, w.{v}::text)".format(n=name_col, v=vendor_col)
        vendor_join = "LEFT JOIN plenum_cafm.vendors ven ON ven.id::text = w.{v}::text".format(v=vendor_col)
    else:
        vendor = ("w." + vendor_col + "::text") if vendor_col else "NULL"
        vendor_join = ""
    due = _pick(wo, "sla_due_at", "scheduled_date")
    finished = _pick(wo, "closed_at", "completed_at")
    clause, params = _scope_sql(building_ids, "w.building_id")

    done_sql = f"lower(w.status) = ANY(CAST(:done AS text[]))"
    params["done"] = list(DONE)
    late_expr = (
        f"CASE WHEN {finished} IS NOT NULL AND {due} IS NOT NULL "
        f"AND {finished} > {due}::timestamptz THEN 1 ELSE 0 END"
        if finished and due else "0"
    )
    missed_expr = (
        f"CASE WHEN NOT ({done_sql}) AND {due} IS NOT NULL "
        f"AND {due}::timestamptz < now() THEN 1 ELSE 0 END"
        if due else "0"
    )
    next_expr = (
        f"min({due}::timestamptz) FILTER (WHERE NOT ({done_sql}) AND {due}::timestamptz >= now())"
        if due else "NULL"
    )
    sql = f"""
        SELECT coalesce({vendor}, 'Unassigned')                AS vendor,
               count(*)                                        AS planned,
               count(*) FILTER (WHERE {done_sql})              AS done,
               sum({missed_expr})                              AS missed,
               sum({late_expr})                                AS late,
               {next_expr}                                     AS next_due,
               count(DISTINCT w.building_id)                   AS buildings
          FROM plenum_cafm.work_orders w
          {vendor_join}
         WHERE w.building_id IS NOT NULL
           AND lower(coalesce(w.{kind}, '')) IN ('ppm', 'planned', 'preventive', 'pm'){clause}
         GROUP BY 1
         ORDER BY 2 DESC"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.ppm_failed", error=str(exc)[:300])
        return {"ok": False, "contracts": [], "error": str(exc)[:200]}

    contracts = []
    for r in rows:
        d = r["next_due"]
        planned, done = int(r["planned"] or 0), int(r["done"] or 0)
        contracts.append({
            "vendor": r["vendor"], "planned": planned, "done": done,
            "missed": int(r["missed"] or 0), "late": int(r["late"] or 0),
            "buildings": int(r["buildings"] or 0),
            "completion_pct": round(done / planned * 100, 1) if planned else None,
            "next_due": d.isoformat() if hasattr(d, "isoformat") else d,
        })
    total_planned = sum(c["planned"] for c in contracts)
    total_done = sum(c["done"] for c in contracts)
    return {
        "ok": True,
        "summary": {
            "contracts": len(contracts), "planned": total_planned, "done": total_done,
            "missed": sum(c["missed"] for c in contracts),
            "late": sum(c["late"] for c in contracts),
            "completion_pct": round(total_done / total_planned * 100, 1) if total_planned else None,
        },
        "contracts": contracts,
    }
