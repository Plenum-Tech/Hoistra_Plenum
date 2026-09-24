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

import json

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from . import inspection_intelligence as ii

log = get_logger(__name__)

_shape: dict[str, set[str]] | None = None

#: Work-order states that put a decision in front of a person, by the spelling each
#: database uses. Matched case-insensitively, so "Open" and "open" are one state.
BLOCKED = ("blocked", "on hold", "onhold", "held", "suspended")
AWAITING = ("pending_approval", "pending approval", "awaiting approval", "submitted", "draft")
LIVE = ("open", "inprogress", "in progress", "in_progress", "assigned", "scheduled")
DONE = ("completed", "closed", "complete", "done", "cancelled", "canceled")

#: How many decisions are read to count them, whatever page size was asked for.
#:
#: The page limit used to be pushed into each source's own SQL, so `total` came back as
#: "min(what exists, the page size), summed per source" — at limit=50 a portfolio with 209
#: decisions reported 100. A count has to be a count. Reading is bounded by this instead, and
#: a response that hits it says so rather than presenting a capped number as the total.
COUNT_CAP = 5000

#: The order the screen lists states in, and the one decisions are sorted by.
STATE_ORDER = {"Blocked": 0, "Deviation": 1, "Awaiting approval": 2, "To raise": 3}

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

#: Every module that can raise one, including the fallback SOURCE_OF applies to an unmapped
#: kind. The filter row is built from this rather than from what today's rows happen to
#: contain, so the same five chips are on the screen whatever the data holds.
SOURCE_LABELS: tuple[str, ...] = tuple(sorted(set(SOURCE_OF.values()) | {"Maintenance"}))


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
                                         'vendor_contracts','buildings','maintenance_plans',
                                         'ppm_visits','spare_parts','compliance_certificates',
                                         'schedule_triggers','ppm_visits')"""
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
    state: str | None = None, source: str | None = None, group_by: str | None = None,
) -> dict[str, Any]:
    """Every decision the property manager owes, across both places one can arrive from.

    A work order carries its own state. An approvals item is a decision that does not have
    a work order yet — the module that raised it says one should exist — so its ``work_order``
    is null and its ``state`` is "To raise". Both are reported in one list because to the
    person owing the decision they are the same queue.

    The screen filters on state (Blocked, To raise, Awaiting approval, Deviation) and on the
    module a decision came from (Compliance, Vendors, Assets, Energy), and groups by state,
    source, building or vendor. All of that happens here rather than in the page, because the
    per-group counts and the cost rollup have to be computed over **every** decision in scope,
    not over whichever ones survived a limit.
    """
    sh = await shape(session)
    wo = sh.get("work_orders", set())
    out: list[dict[str, Any]] = []
    # Read to the cap rather than to the page size: the counts below are over everything in
    # scope, and slicing to `limit` happens once, at the end.
    fetch = COUNT_CAP

    # Both spellings exist on one database and only one is populated per row, so take the
    # first that is actually there rather than the first that exists as a column.
    # The identifier a decision prints, in the order a person would want it: a code somebody
    # can read first, then the reference, then the uuid identity. wo_uuid is last because a
    # uuid is a poor thing to show — but it is always there, so a row is never blank.
    have = [c for c in ("wo_code", "work_order_id", "workorder_ref", "wo_uuid") if c in wo]
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
                       "live": list(LIVE), "lim": fetch})

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
                   {vendor_id}         AS vendor_id,
                   {asset}             AS asset,
                   {asset_id}          AS asset_id,
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
            asset_id=("w.asset_id::text" if "asset_id" in wo else "NULL"),
            vendor_id=(("w." + vendor_col + "::text") if vendor_col else "NULL"),
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
            # Named row_state, not state: `state` is this function's filter parameter, and
            # rebinding it here silently turned every filtered request into an unfiltered one.
            row_state = r["state"]
            trigger = {
                "Blocked": "Work order held at status " + repr(r["status"]),
                "Awaiting approval": "Approval outstanding",
                "Deviation": "Past its due date",
            }.get(row_state, row_state)
            # A blocked or deviating order with a vendor on it is a vendor problem — the
            # thing standing in the way is the vendor, and that is the queue it belongs in.
            # Anything else is plain maintenance. Approvals items bring their own real
            # source, so this only decides the work-order rows.
            attributed = ("Vendors" if row_state in ("Blocked", "Deviation") and r["vendor"]
                          else "Maintenance")
            out.append({
                "work_order": r["code"], "state": row_state, "source": attributed,
                "trigger": trigger, "detail": r["title"],
                "asset": r["asset"], "asset_id": r["asset_id"],
                "building": r["building"], "building_id": r["building_id"],
                "vendor": r["vendor"], "vendor_id": r["vendor_id"],
                "estimated_cost": _num(r["est"]),
                "priority": r["priority"], "due": r["due"],
            })

    out.extend(await _decisions_from_approvals(session, building_ids=building_ids, limit=fetch))
    out.sort(key=lambda d: (STATE_ORDER.get(d["state"], 9), str(d.get("building") or "")))

    # Counted over everything in scope, so the tallies do not change when a filter narrows
    # the list. The screen shows "10 of 10 decisions": the first number is what came back,
    # the second is what exists.
    by_state = {k: sum(1 for d in out if d["state"] == k) for k in STATE_ORDER}
    by_source = _tally(out, "source")
    total = len(out)
    capped = total >= fetch

    kept = [d for d in out
            if (state is None or d["state"] == state)
            and (source is None or d["source"] == source)]

    page = kept[:limit]
    return {
        "ok": True,
        # Three numbers that each mean one thing: how many came back in this response, how
        # many survived the filter, and how many exist in scope. They were previously one
        # number wearing three hats, which is why a page could print "134 of 134" while
        # showing a fraction of them.
        "count": len(page),
        "matched": len(kept),
        "total": total,
        # True when there are more decisions than were read, so `total` is a floor rather than
        # the figure. A capped count presented as the total is how "134 of 134" ends up on a
        # screen that is showing 134 of two thousand.
        "total_is_capped": capped,
        "read_cap": fetch,
        "filtered": state is not None or source is not None,
        "by_state": by_state,
        "by_source": by_source,
        # Every state and every module, whether or not today's rows reach them.
        #
        # This listed only what had rows, so the filter row changed shape with the data: a
        # portfolio mid-ingest showed four chips where a populated one showed eight, and the
        # same screen in two environments read as a missing feature rather than an empty
        # category. A chip at 0 is a fact about the queue — "nothing is deviating" is worth
        # knowing, and it is not the same as "deviation is not a thing here".
        #
        # by_state / by_source still carry counts only for what exists, so the client reads a
        # missing key as 0. That is safe precisely because this list is now exhaustive: the
        # read answered, and the category it did not mention is empty rather than unknown.
        #
        # The union with by_source keeps a kind SOURCE_OF has not been taught yet visible
        # instead of dropping its rows out of the filter row entirely.
        "available": {
            "state": list(STATE_ORDER),
            "source": sorted(set(SOURCE_LABELS) | set(by_source)),
            "group_by": list(GROUP_KEYS),
        },
        "group_by": group_by,
        "groups": _group(kept, group_by, limit=limit) if group_by else None,
        "decisions": page,
    }


#: What a group can be cut by, and the field each one reads.
GROUP_KEYS = {"state": "state", "source": "source",
              "building": "building", "vendor": "vendor"}


def _group(rows: list[dict[str, Any]], by: str,
           *, limit: int | None = None) -> list[dict[str, Any]]:
    """The decisions cut into the groups the screen offers, each with its own rollup.

    Every group carries the two counts the header shows — how many are blocked and how many
    are still to raise — and the money in it. A row with no value for the grouping field is
    gathered under a stated label rather than dropped, because a decision with no vendor is
    still a decision somebody owes.
    """
    field = GROUP_KEYS.get(by)
    if not field:
        return []
    unlabelled = {"building": "No building", "vendor": "Unassigned",
                  "source": "Unattributed", "state": "No state"}[by]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(str(r.get(field) or unlabelled), []).append(r)

    out = []
    for key, items in buckets.items():
        costs = [d["estimated_cost"] for d in items if d["estimated_cost"] is not None]
        out.append({
            "key": key,
            "count": len(items),
            "blocked": sum(1 for d in items if d["state"] == "Blocked"),
            "to_raise": sum(1 for d in items if d["state"] == "To raise"),
            "deviating": sum(1 for d in items if d["state"] == "Deviation"),
            "awaiting_approval": sum(1 for d in items if d["state"] == "Awaiting approval"),
            # None, not 0, when nothing in the group carries an estimate — a group whose
            # cost is unknown must not read as a group that costs nothing.
            "estimated_cost": round(sum(costs), 2) if costs else None,
            "priced": len(costs),
            # The counts above are over every decision in the group; the list is a page of
            # them. A rollup that counted only what it returned would be a rollup of nothing.
            "decisions": items[:limit] if limit else items,
        })
    # State groups keep the order the screen lists them in; everything else leads with the
    # biggest group, which is what a person scanning the page is looking for.
    if by == "state":
        out.sort(key=lambda g: STATE_ORDER.get(g["key"], 9))
    else:
        out.sort(key=lambda g: (-g["count"], g["key"]))
    return out


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
    cc = sh.get("compliance_certificates", set())
    cert_expiry = "c." + (_pick(cc, "expiry_date", "next_due_date") or "created_at")         if cc else "NULL"
    cert_ref = "c." + (_pick(cc, "certificate_number", "certificate_ref") or "id") if cc else "NULL"
    cert_kind = "c." + (_pick(cc, "certificate_type_code", "cert_type") or "id") if cc else "NULL"
    resolved = " COALESCE(" + ", ".join([x for x in (anomaly_b, cert_b) if x] or ["NULL"]) + ")::text"
    where_scope = f" AND {resolved} = ANY(CAST(:scope_b AS text[]))" if restricted else ""
    sql = f"""
        SELECT q.id::text AS id, q.related_entity_type AS kind, q.summary AS summary,
               q.severity AS severity, q.source_feature AS feature,
               {resolved} AS building_id, b.name AS building,
               coalesce(a.asset_id::text, c.asset_id::text) AS asset_id,
               c.vendor_id::text                            AS vendor_id,
               {cert_expiry}                                AS cert_expires,
               {cert_ref}                                   AS cert_ref,
               {cert_kind}                                  AS cert_kind
          FROM plenum_cafm.approvals_queue_items q
          {joins}
          LEFT JOIN plenum_cafm.buildings b ON b.building_id::text = {resolved}
         WHERE q.status = 'pending' AND {resolved} IS NOT NULL{where_scope}
         ORDER BY q.created_at DESC NULLS LAST
         LIMIT :lim"""
    sql = sql.format(cert_expiry=cert_expiry, cert_ref=cert_ref, cert_kind=cert_kind)
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
        "asset_id": r["asset_id"],
        "building": r["building"], "building_id": r["building_id"],
        "vendor": None, "vendor_id": r["vendor_id"], "estimated_cost": None,
        "priority": r["severity"], "due": None, "queue_item": r["id"],
        "certificate_expires": _iso_date(r["cert_expires"]),
        "certificate": r["cert_ref"], "certificate_type": r["cert_kind"],
    } for r in rows]


def _iso_date(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


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


# ── next PPM date, per asset ─────────────────────────────────────────────────────────

#: How long each spelling of a frequency actually is, in months. The two databases spell the
#: same cadence four ways between them ("monthly", "Monthly", "Six-monthly", "Quarterly"), so
#: matching is lower-cased and punctuation-insensitive. A frequency not in here is reported as
#: unrecognised rather than guessed at — a wrong interval produces a confident wrong date,
#: which is worse than no date.
FREQUENCY_MONTHS: dict[str, float] = {
    "weekly": 0.25, "fortnightly": 0.5, "biweekly": 0.5, "twoweekly": 0.5,
    "monthly": 1, "fourweekly": 1,
    "bimonthly": 2, "twomonthly": 2,
    "quarterly": 3, "threemonthly": 3,
    "fourmonthly": 4,
    "sixmonthly": 6, "halfyearly": 6, "biannual": 6, "semiannual": 6,
    "annual": 12, "annually": 12, "yearly": 12, "onceayear": 12,
    "biennial": 24, "twoyearly": 24,
    "fiveyearly": 60, "quinquennial": 60,
}

#: The words a work order uses for planned work, in either database.
PLANNED_KINDS = ("ppm", "planned", "preventive", "preventative", "pm", "scheduled")


def _freq_months(raw: Any) -> float | None:
    """Months between visits, from however this database spells the frequency."""
    if raw is None:
        return None
    key = "".join(ch for ch in str(raw).lower() if ch.isalnum())
    return FREQUENCY_MONTHS.get(key)


#: Sub-month cadences in exact days. A weekly visit is seven days later, not the 7.6 that
#: falls out of treating a quarter of an average month as a duration.
SUB_MONTH_DAYS: dict[float, int] = {0.25: 7, 0.5: 14}


def _add_months(d: date, months: float) -> date:
    """Calendar-correct month arithmetic, clamped to the end of a short month.

    A quarterly visit last done on 31 January falls due on 30 April, not 1 May. Cadences
    shorter than a month are added as an exact number of days.
    """
    if months < 1:
        return d + timedelta(days=SUB_MONTH_DAYS.get(months, round(months * 30.44)))
    whole = int(months)
    year = d.year + (d.month - 1 + whole) // 12
    month = (d.month - 1 + whole) % 12 + 1
    last = monthrange(year, month)[1]
    out = date(year, month, min(d.day, last))
    rest = months - whole
    return out + timedelta(days=round(rest * 30.44)) if rest else out


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any], what: str) -> list:
    """One source, in its own savepoint: a table this database shapes differently must not
    abort the transaction and take the other four sources down with it."""
    try:
        async with session.begin_nested():
            # dict, not RowMapping: callers annotate these rows, and a RowMapping is read-only.
            return [dict(r) for r in
                    (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.query_failed", source=what, error=str(exc)[:200])
        return []


async def next_ppm(
    session: AsyncSession, *, building_ids: list[UUID] | None,
    asset_id: str | None = None, only: str | None = None, limit: int = 500,
) -> dict[str, Any]:
    """When each asset is next due a planned visit, and on whose authority.

    There are five places a next date can come from and they are not equally good. A date
    somebody booked is a commitment; a date computed from how often the last few visits
    happened is arithmetic. Both are useful and they are not the same claim, so every asset
    says which it got:

    * ``booked`` — a real date on record: a maintenance plan's next due date, a schedule
      trigger, an uncompleted visit, or an open planned work order.
    * ``projected`` — no date on record, but the asset has a completed visit and a frequency,
      so the next one is that frequency after the last one. Clearly labelled, because it is
      a forecast and the visit may never be booked.
    * ``unknown`` — neither. Reported as null with a reason, never as a date.

    An asset with no PPM history at all is the normal case on a portfolio that has never had
    a schedule loaded, and the summary says so rather than leaving the page to infer it from
    an empty list.
    """
    sh = await shape(session)
    today = datetime.now(timezone.utc).date()
    clause, params = _scope_sql(building_ids, "a.building_id")
    if asset_id:
        clause += " AND a.id::text = :one_asset"
        params["one_asset"] = str(asset_id)

    assets = await _rows(session, f"""
        SELECT a.id::text AS id, a.asset_name, a.asset_code,
               a.building_id::text AS building_id, b.name AS building
          FROM plenum_cafm.assets a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.building_id IS NOT NULL{clause}
         ORDER BY a.asset_name""", params, "assets")
    if not assets:
        return {"ok": True, "assets": [], "summary": _ppm_summary([], 0),
                "note": "no assets in scope"}

    ids = [a["id"] for a in assets]
    idp = {"aids": ids}

    # ── the booked sources, best authority first ─────────────────────────────────────
    booked: dict[str, tuple[date, str, Any]] = {}

    def offer(asset: str, when: Any, source: str, freq: Any = None) -> None:
        """Keep the earliest booked date per asset; ties keep the higher-authority source."""
        if when is None:
            return
        when = when.date() if isinstance(when, datetime) else when
        if not isinstance(when, date):
            return
        seen = booked.get(asset)
        if seen is None or when < seen[0]:
            booked[asset] = (when, source, freq)

    mp = sh.get("maintenance_plans", set())
    if {"asset_id", "next_due_date"} <= mp:
        status = " AND lower(coalesce(p.status,'active')) NOT IN ('cancelled','inactive')" \
            if "status" in mp else ""
        freq_sel = "p.frequency_type" if "frequency_type" in mp else "NULL"
        for r in await _rows(session, f"""
            SELECT p.asset_id::text AS id, min(p.next_due_date) AS due, min({freq_sel}) AS freq
              FROM plenum_cafm.maintenance_plans p
             WHERE p.asset_id::text = ANY(CAST(:aids AS text[]))
               AND p.next_due_date IS NOT NULL{status}
             GROUP BY 1""", idp, "maintenance_plans"):
            offer(r["id"], r["due"], "maintenance plan", r["freq"])

    st = sh.get("schedule_triggers", set())
    if {"maintenance_plan_id", "next_due_date"} <= st and "asset_id" in mp:
        for r in await _rows(session, """
            SELECT p.asset_id::text AS id, min(t.next_due_date) AS due,
                   min(t.interval_unit) AS unit, min(t.interval_value) AS val
              FROM plenum_cafm.schedule_triggers t
              JOIN plenum_cafm.maintenance_plans p ON p.id = t.maintenance_plan_id
             WHERE p.asset_id::text = ANY(CAST(:aids AS text[]))
               AND t.next_due_date IS NOT NULL
             GROUP BY 1""", idp, "schedule_triggers"):
            offer(r["id"], r["due"], "schedule trigger",
                  f"{r['val']} {r['unit']}" if r["val"] and r["unit"] else None)

    pv = sh.get("ppm_visits", set())
    has_visits = {"asset_id", "scheduled_date"} <= pv
    if has_visits:
        for r in await _rows(session, """
            SELECT v.asset_id::text AS id, min(v.scheduled_date) AS due, min(v.frequency) AS freq
              FROM plenum_cafm.ppm_visits v
             WHERE v.asset_id::text = ANY(CAST(:aids AS text[]))
               AND v.completed_date IS NULL AND v.scheduled_date IS NOT NULL
             GROUP BY 1""", idp, "ppm_visits_open"):
            offer(r["id"], r["due"], "booked visit", r["freq"])

    wo = sh.get("work_orders", set())
    kind = _pick(wo, "wo_type", "maintenance_type", "request_type")
    due_col = _pick(wo, "scheduled_date", "sla_due_at")
    if kind and due_col and "asset_id" in wo:
        for r in await _rows(session, f"""
            SELECT w.asset_id::text AS id, min(w.{due_col}::text) AS due
              FROM plenum_cafm.work_orders w
             WHERE w.asset_id::text = ANY(CAST(:aids AS text[]))
               AND w.{due_col} IS NOT NULL AND w.{due_col}::text <> ''
               AND lower(coalesce(w.{kind}, '')) = ANY(CAST(:kinds AS text[]))
               AND NOT (lower(w.status) = ANY(CAST(:done AS text[])))
             GROUP BY 1""",
            {**idp, "kinds": list(PLANNED_KINDS), "done": list(DONE)}, "work_orders_open"):
            offer(r["id"], _as_date(r["due"]), "booked work order")

    # ── history, for the assets nothing has booked ───────────────────────────────────
    history: dict[str, tuple[date, Any, str]] = {}

    def remember(asset: str, when: Any, freq: Any, source: str) -> None:
        when = when.date() if isinstance(when, datetime) else when
        if not isinstance(when, date):
            return
        seen = history.get(asset)
        if seen is None or when > seen[0]:
            history[asset] = (when, freq, source)

    if has_visits and "completed_date" in pv:
        for r in await _rows(session, """
            SELECT v.asset_id::text AS id, max(v.completed_date) AS last,
                   count(*) AS visits,
                   (array_agg(v.frequency ORDER BY v.completed_date DESC))[1] AS freq
              FROM plenum_cafm.ppm_visits v
             WHERE v.asset_id::text = ANY(CAST(:aids AS text[]))
               AND v.completed_date IS NOT NULL
             GROUP BY 1""", idp, "ppm_visits_history"):
            remember(r["id"], r["last"], r["freq"], "completed visits")

    finished = _pick(wo, "closed_at", "completed_at")
    if kind and finished and "asset_id" in wo:
        for r in await _rows(session, f"""
            SELECT w.asset_id::text AS id, max(w.{finished}) AS last
              FROM plenum_cafm.work_orders w
             WHERE w.asset_id::text = ANY(CAST(:aids AS text[]))
               AND w.{finished} IS NOT NULL
               AND lower(coalesce(w.{kind}, '')) = ANY(CAST(:kinds AS text[]))
             GROUP BY 1""",
            {**idp, "kinds": list(PLANNED_KINDS)}, "work_orders_history"):
            remember(r["id"], r["last"], None, "completed planned orders")

    # ── merge: booked beats projected beats nothing ──────────────────────────────────
    out: list[dict[str, Any]] = []
    for a in assets:
        row = {
            "asset_id": a["id"], "asset_name": a["asset_name"], "asset_code": a["asset_code"],
            "building_id": a["building_id"], "building": a["building"],
            "next_ppm_date": None, "confidence": "unknown", "source": None,
            "frequency": None, "interval_months": None, "last_ppm_date": None,
            "days_until": None, "overdue": False, "superseded_by_completion": False,
            "basis": None,
        }
        last = history.get(a["id"])
        if last:
            row["last_ppm_date"] = last[0].isoformat()

        hit = booked.get(a["id"])
        if hit:
            when, source, freq = hit
            # A booked date earlier than the asset's last completed visit is almost always
            # a row nobody closed out, not work that is months late. It stays booked and
            # overdue because that is what the record says, but it is flagged, so a page can
            # stop short of alarming somebody about a visit that has in fact been overtaken.
            stale = bool(last and when < last[0])
            row.update({
                "next_ppm_date": when.isoformat(), "confidence": "booked", "source": source,
                "frequency": str(freq) if freq else None,
                "interval_months": _freq_months(freq),
                "days_until": (when - today).days, "overdue": when < today,
                "superseded_by_completion": stale,
                "basis": (
                    f"a date on record, from the {source}, but a later visit was completed "
                    f"on {last[0].isoformat()} — the booking was probably never closed out"
                    if stale else f"a date on record, from the {source}"),
            })
        elif last:
            when_last, freq, source = last
            months = _freq_months(freq)
            if months:
                nxt = _add_months(when_last, months)
                row.update({
                    "next_ppm_date": nxt.isoformat(), "confidence": "projected",
                    "source": "projected from cadence", "frequency": str(freq),
                    "interval_months": months, "days_until": (nxt - today).days,
                    "overdue": nxt < today,
                    "basis": (f"no visit is booked; projected as {freq} after the last "
                              f"{source[:-1]} on {when_last.isoformat()}"),
                })
            else:
                row["basis"] = (
                    f"last {source[:-1]} was {when_last.isoformat()}, but its frequency "
                    + (f"({freq!r}) is not one this service recognises"
                       if freq else "is not recorded")
                    + ", so the next date cannot be projected")
        else:
            row["basis"] = "no maintenance plan, no booked visit and no completed planned work"
        out.append(row)

    out.sort(key=lambda r: (r["next_ppm_date"] is None, r["next_ppm_date"] or "",
                            r["asset_name"] or ""))
    return {
        "ok": True,
        "assets": out[:limit] if only is None else [
            r for r in out if r["confidence"] == only][:limit],
        "summary": _ppm_summary(out, len(assets)),
        "note": (
            "booked is a date somebody committed to; projected is that asset's own cadence "
            "applied to its last completed visit, which may never be booked; unknown is "
            "reported as null rather than as a guess"
        ),
    }


def _as_date(v: Any) -> date | None:
    """A date from a column that holds one as text on one database and a date on the other."""
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _ppm_summary(rows: list[dict[str, Any]], total: int) -> dict[str, Any]:
    booked = sum(1 for r in rows if r["confidence"] == "booked")
    projected = sum(1 for r in rows if r["confidence"] == "projected")
    return {
        "assets": total,
        "booked": booked,
        "projected": projected,
        "unknown": sum(1 for r in rows if r["confidence"] == "unknown"),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "due_next_30_days": sum(
            1 for r in rows if r["days_until"] is not None and 0 <= r["days_until"] <= 30),
        # Counted separately so an overdue figure is not inflated by stale bookings.
        "overdue_superseded": sum(1 for r in rows if r["superseded_by_completion"]),
        "scheduled_anywhere": booked > 0,
    }


# ── PPM health, by contract, year to date ────────────────────────────────────────────

#: When a contract is behind plan, watched, or running to plan. Thresholds rather than a
#: gut call, so two people reading the table reach the same conclusion — and so moving them
#: is a decision somebody makes once rather than an argument every month.
BEHIND_MISSED = 3          # this many missed visits is behind plan whatever the percentage
BEHIND_COMPLETION_PCT = 80.0
WATCH_COMPLETION_PCT = 100.0

#: A visit that was agreed to move is not one nobody turned up for.
PPM_STATE_BEHIND = "behind plan"
PPM_STATE_WATCH = "watch"
PPM_STATE_TO_PLAN = "to plan"


def ppm_state(*, missed: int, late: int, completion_pct: float | None) -> str:
    """Behind plan, watch, or to plan — from the counts, by a stated rule.

    Missed visits carry more weight than late ones: a visit that happened late still happened,
    and a visit that never happened is the one the contract is judged on.
    """
    pct = 100.0 if completion_pct is None else completion_pct
    if missed >= BEHIND_MISSED or pct < BEHIND_COMPLETION_PCT:
        return PPM_STATE_BEHIND
    if missed or late or pct < WATCH_COMPLETION_PCT:
        return PPM_STATE_WATCH
    return PPM_STATE_TO_PLAN


async def ppm_health_by_contract(
    session: AsyncSession, *, building_ids: list[UUID] | None,
    year_to_date: bool = True, limit: int = 200,
) -> dict[str, Any]:
    """The PPM health table: one row per contract, year to date, with its state.

    Keyed on the **contract** rather than the vendor, because that is the thing with a scope
    and a plan — one vendor can hold several contracts covering different services, and rolling
    them together hides the one that is behind.

    Visits come from ``ppm_visits`` where that table has rows, and fall back to planned work
    orders where it does not. Which source answered is stated on every row: a table built from
    two different sources without saying which is a table nobody can check.
    """
    sh = await shape(session)
    today = datetime.now(timezone.utc).date()
    start = date(today.year, 1, 1) if year_to_date else date(1900, 1, 1)

    pv = sh.get("ppm_visits", set())
    vc = sh.get("vendor_contracts", set())
    have_visits = {"asset_id", "scheduled_date"} <= pv
    if not have_visits:
        return {"ok": True, "contracts": [], "summary": _ppm_zero(),
                "source": None,
                "note": "no ppm_visits table on this database; nothing to report against"}

    # Which optional columns this database actually has.
    deferred = "v.deferred" if "deferred" in pv else "FALSE"
    # ::text on both sides: vendor_contracts.id is integer on one database and uuid on
    # the other, and ppm_visits.contract_id does not always agree with either.
    contract_join = ("LEFT JOIN plenum_cafm.vendor_contracts c ON c.id::text = v.contract_id::text"
                     if "contract_id" in pv and vc else "")
    c_name = "c.contract_name" if "contract_name" in vc else "NULL"
    c_country = "c.country_code" if "country_code" in vc else "NULL"
    c_scope = "c.service_scope" if "service_scope" in vc else "NULL"
    c_plan = "c.visits_per_year" if "visits_per_year" in vc else "NULL"
    ven_name = "ven.vendor_name" if "vendor_name" in sh.get("vendors", set()) else "ven.name"
    report_on = ("v.inspection_id IS NOT NULL" if "inspection_id" in pv
                 else "FALSE")

    clause, params = _scope_sql(building_ids, "a.building_id")
    params["start"] = start
    sql = f"""
        SELECT coalesce({c_name}, {ven_name}, 'Unassigned')      AS contract,
               v.contract_id::text                               AS contract_id,
               {ven_name}                                        AS vendor,
               {c_country}                                       AS country_code,
               {c_scope}                                         AS service_scope,
               {c_plan}                                          AS visits_per_year,
               count(*)                                          AS planned,
               count(*) FILTER (WHERE v.completed_date IS NOT NULL)          AS done,
               count(*) FILTER (WHERE v.completed_date IS NULL
                                  AND v.scheduled_date < current_date
                                  AND NOT {deferred})                        AS missed,
               -- Late means past the tolerance the contract agrees, not past the date. A
               -- visit booked for the 1st and done on the 3rd inside a 7-day window was on
               -- time, and counting it late makes every contract look worse than it is.
               count(*) FILTER (WHERE v.completed_date IS NOT NULL
                                  AND v.scheduled_date IS NOT NULL
                                  AND v.completed_date > v.scheduled_date
                                      + make_interval(days => coalesce(v.tolerance_days, 0)))
                   AS late,
               count(*) FILTER (WHERE {deferred})                            AS deferred,
               count(*) FILTER (WHERE {report_on})                           AS reports,
               min(v.scheduled_date) FILTER (WHERE v.completed_date IS NULL
                                               AND v.scheduled_date >= current_date) AS next_due,
               count(DISTINCT a.building_id)                     AS buildings
          FROM plenum_cafm.ppm_visits v
          JOIN plenum_cafm.assets a ON a.id::text = v.asset_id::text
          LEFT JOIN plenum_cafm.vendors ven ON ven.id::text = v.vendor_id::text
          {contract_join}
         WHERE a.building_id IS NOT NULL
           AND v.scheduled_date >= :start{clause}
         GROUP BY 1, 2, 3, 4, 5, 6
         ORDER BY 1"""
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql), params)).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.ppm_by_contract_failed", error=str(exc)[:300])
        return {"ok": False, "error": str(exc)[:200], "contracts": [],
                "summary": _ppm_zero()}

    out = []
    for r in rows:
        planned = int(r["planned"] or 0)
        done = int(r["done"] or 0)
        missed = int(r["missed"] or 0)
        late = int(r["late"] or 0)
        # The plan is what the contract commits to where that is agreed, and what was actually
        # booked where it is not. Which one was used is stated, because 12 of 18 against an
        # agreed plan and 12 of 18 against whatever got booked are different claims.
        committed = int(r["visits_per_year"]) if r["visits_per_year"] else None
        plan = committed or planned
        pct = round(done / plan * 100, 1) if plan else None
        out.append({
            "contract": r["contract"], "contract_id": r["contract_id"],
            "vendor": r["vendor"], "country_code": r["country_code"],
            "service_scope": r["service_scope"],
            "visits_to_plan": {"done": done, "plan": plan,
                               "plan_is_committed": committed is not None},
            "planned": planned, "done": done, "missed": missed, "late": late,
            "deferred": int(r["deferred"] or 0),
            "reports": int(r["reports"] or 0),
            "reports_to_done": {"filed": int(r["reports"] or 0), "done": done},
            "completion_pct": pct,
            "next_due": r["next_due"].isoformat() if r["next_due"] else None,
            "buildings": int(r["buildings"] or 0),
            "state": ppm_state(missed=missed, late=late, completion_pct=pct),
        })
    out.sort(key=lambda c: ({PPM_STATE_BEHIND: 0, PPM_STATE_WATCH: 1,
                             PPM_STATE_TO_PLAN: 2}[c["state"]], -c["missed"], c["contract"]))

    done = sum(c["done"] for c in out)
    plan = sum(c["visits_to_plan"]["plan"] for c in out)
    return {
        "ok": True,
        "source": "ppm_visits",
        "window": {"year_to_date": year_to_date, "from": start.isoformat(),
                   "to": today.isoformat()},
        "summary": {
            "contracts": len(out),
            "done": done, "plan": plan,
            "completion_pct": round(done / plan * 100, 1) if plan else None,
            "missed": sum(c["missed"] for c in out),
            "late": sum(c["late"] for c in out),
            "deferred": sum(c["deferred"] for c in out),
            "reports": sum(c["reports"] for c in out),
            "behind_plan": sum(1 for c in out if c["state"] == PPM_STATE_BEHIND),
            "watch": sum(1 for c in out if c["state"] == PPM_STATE_WATCH),
            "to_plan": sum(1 for c in out if c["state"] == PPM_STATE_TO_PLAN),
        },
        "contracts": out[:limit],
        "rule": (
            f"behind plan at {BEHIND_MISSED}+ missed visits or under "
            f"{BEHIND_COMPLETION_PCT:g}% complete; watch at any missed, any late or under "
            f"{WATCH_COMPLETION_PCT:g}%; to plan otherwise. A deferred visit is not a missed one."
        ),
    }


def _ppm_zero() -> dict[str, Any]:
    return {"contracts": 0, "done": 0, "plan": 0, "completion_pct": None, "missed": 0,
            "late": 0, "deferred": 0, "reports": 0, "behind_plan": 0, "watch": 0, "to_plan": 0}


# ── the four cards across the top of the Maintenance screen ──────────────────────────

#: A certificate this close to running out is treated the same as one that already has: the
#: work has to be booked now either way, which is what makes the decision statutory.
STATUTORY_WINDOW_DAYS = 30


async def statutory_assets(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, dict[str, Any]]:
    """Assets and vendors whose certificate has lapsed or runs out inside the window.

    Returned as a lookup rather than a count, so a decision can be marked statutory *and* say
    which certificate makes it so. A count on its own is a number nobody can check.
    """
    sh = await shape(session)
    if "compliance_certificates" not in sh:
        return {}
    cc = sh["compliance_certificates"]
    expiry = _pick(cc, "expiry_date", "next_due_date")
    if not expiry:
        return {}
    ref = _pick(cc, "certificate_number", "certificate_ref") or "NULL"
    kind = _pick(cc, "certificate_type_code", "cert_type") or "NULL"

    rows = await _rows(session, f"""
        SELECT c.asset_id::text  AS asset_id,
               c.vendor_id::text AS vendor_id,
               c.{expiry}        AS expires,
               c.{ref}           AS certificate,
               c.{kind}          AS certificate_type,
               (c.{expiry} < current_date) AS lapsed
          FROM plenum_cafm.compliance_certificates c
         WHERE c.{expiry} IS NOT NULL
           AND c.{expiry} <= current_date + make_interval(days => :win)
           AND (c.asset_id IS NOT NULL OR c.vendor_id IS NOT NULL)""",
        {"win": STATUTORY_WINDOW_DAYS}, "statutory_certificates")

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        entry = {
            "certificate": r["certificate"], "certificate_type": r["certificate_type"],
            "expires": r["expires"].isoformat() if r["expires"] else None,
            "lapsed": bool(r["lapsed"]),
        }
        # The soonest expiry wins where an asset carries several: that is the one forcing it.
        for key in (f"asset:{r['asset_id']}" if r["asset_id"] else None,
                    f"vendor:{r['vendor_id']}" if r["vendor_id"] else None):
            if key and (key not in out or (entry["expires"] or "") < (out[key]["expires"] or "~")):
                out[key] = entry
    return out


async def overview(
    session: AsyncSession, *, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """The four cards across the top of the Maintenance screen, in one call.

    Each card returns the sub-counts printed underneath it, not just the headline number —
    "2 blocked · 4 to raise · 2 deviating" is the part that tells somebody what to do next.
    """
    dec = await decisions(session, building_ids=building_ids, limit=1000)
    statutory = await statutory_assets(session, building_ids=building_ids)

    # Which decisions are statutory, and why. Marked on the decision itself so the chip and
    # the card agree by construction rather than by two counts happening to match.
    #
    # Two ways a decision qualifies. A decision raised *about* a certificate is tested against
    # that certificate's own expiry — exact, and the only test that catches building-scope
    # certificates like an EICR, which carry neither an asset nor a vendor and are most of
    # them. Anything else is matched on the asset or the vendor it names.
    horizon = (datetime.now(timezone.utc).date() + timedelta(days=STATUTORY_WINDOW_DAYS))
    today = datetime.now(timezone.utc).date()
    marked = 0
    for d in dec.get("decisions", []):
        hit = None
        expires = d.get("certificate_expires")
        if expires:
            try:
                when = date.fromisoformat(str(expires)[:10])
            except ValueError:
                when = None
            if when and when <= horizon:
                hit = {"certificate": d.get("certificate"),
                       "certificate_type": d.get("certificate_type"),
                       "expires": when.isoformat(), "lapsed": when < today,
                       "matched_on": "the certificate this decision is about"}
        if hit is None:
            keyed = (statutory.get(f"asset:{d.get('asset_id')}")
                     or statutory.get(f"vendor:{d.get('vendor_id')}"))
            if keyed:
                hit = {**keyed, "matched_on": "a certificate on its asset or vendor"}
        d["statutory"] = bool(hit)
        d["statutory_certificate"] = hit
        if hit:
            marked += 1

    ins = await ii.panel(session, building_ids=building_ids)
    unconv = ins["cards"]["unconverted_recommendations"]
    ppm = await ppm_health_by_contract(session, building_ids=building_ids, year_to_date=True)
    ps = ppm.get("summary", {})

    by_state = dec.get("by_state", {})
    return {
        "ok": True,
        "cards": {
            "decisions_owed": {
                "value": dec.get("total", dec.get("count", 0)),
                "blocked": by_state.get("Blocked", 0),
                "to_raise": by_state.get("To raise", 0),
                "deviating": by_state.get("Deviation", 0),
                "awaiting_approval": by_state.get("Awaiting approval", 0),
                "caption": (f"{by_state.get('Blocked', 0)} blocked · "
                            f"{by_state.get('To raise', 0)} to raise · "
                            f"{by_state.get('Deviation', 0)} deviating"),
            },
            "statutory": {
                "value": marked,
                "of_decisions": dec.get("total", 0),
                "window_days": STATUTORY_WINDOW_DAYS,
                "caption": f"certificate lapsed or inside {STATUTORY_WINDOW_DAYS} days",
                "answerable": bool(statutory) or marked == 0,
                # The decisions themselves, so "which ones?" is one click rather than a
                # second request the page has to work out how to make.
                "decisions": [d for d in dec.get("decisions", [])
                              if d.get("statutory")][:50],
            },
            "recommendations_unconverted": {
                "value": unconv.get("count"),
                "answerable": unconv.get("answerable", False),
                "reason": unconv.get("reason"),
                "now_flagged_by_energy": unconv.get("now_flagged_by_energy"),
                "reports": ins["corpus"]["reports"],
                "since": ins["corpus"]["since"],
                "caption": (f"from {ins['corpus']['reports']} inspection reports"
                            + (f" since {ins['corpus']['since']}"
                               if ins["corpus"]["since"] else "")),
            },
            "ppm_to_plan": {
                "value": ps.get("completion_pct"),
                "done": ps.get("done", 0), "plan": ps.get("plan", 0),
                "missed": ps.get("missed", 0), "reports": ps.get("reports", 0),
                "deferred": ps.get("deferred", 0),
                "behind_plan": ps.get("behind_plan", 0),
                "caption": (f"{ps.get('done', 0)} of {ps.get('plan', 0)} visits · "
                            f"{ps.get('missed', 0)} missed · "
                            f"{ps.get('reports', 0)} reports"),
                "year_to_date": True,
            },
        },
        "last_read": await last_inspection_read(session, building_ids=building_ids),
        "note": ("every card carries the sub-counts printed beneath it; a card that cannot be "
                 "answered on this database says so rather than returning zero"),
    }


async def read_inspection_reports(
    session: AsyncSession, *, building_ids: list[UUID] | None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """What the Re-read inspection reports button calls: read them all together, and record it.

    The panel computes live on every read, so this is not what makes the numbers appear. What
    it adds is a timestamp somebody can point at and a row saying what this reading found, so
    next week's can be compared with it.
    """
    started = datetime.now(timezone.utc)
    p = await ii.panel(session, building_ids=building_ids)
    c = p["cards"]

    def val(name: str) -> int:
        v = c[name].get("count")
        return int(v) if isinstance(v, int) else 0

    scope = "portfolio" if building_ids is None else f"{len(building_ids)} building(s)"
    try:
        async with session.begin_nested():
            await session.execute(text("""
                INSERT INTO plenum_cafm.inspection_read_runs
                    (organization_id, started_at, finished_at, reports_read, assets_covered,
                     unconverted, corroborated_anomalies, warranted_findings, poorly_graded,
                     unanswerable, scope)
                VALUES (CAST(:org AS uuid), :started, now(), :reports, :assets, :unconv,
                        :corr, :warr, :poor, CAST(:unans AS jsonb), :scope)"""),
                {"org": str(organization_id) if organization_id else None,
                 "started": started, "reports": p["corpus"]["reports"],
                 "assets": p["corpus"]["assets"], "unconv": val("unconverted_recommendations"),
                 "corr": val("corroborated_anomalies"), "warr": val("warranted_findings"),
                 "poor": val("poorly_graded"),
                 "unans": json.dumps(p["unanswerable"]), "scope": scope})
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("maintenance.inspection_read_failed", error=str(exc)[:300])
        return {**p, "recorded": False, "error": str(exc)[:200],
                "note": "the reports were read but the run could not be recorded"}
    return {**p, "recorded": True, "scope": scope, "started_at": started.isoformat()}


async def last_inspection_read(
    session: AsyncSession, *, building_ids: list[UUID] | None = None,
    organization_id: UUID | str | None = None,
) -> dict[str, Any] | None:
    """When the reports were last re-read, for ONE company. Null before the first time —
    which means no read has been recorded, not zero.

    The company predicate is the point: without it this answered from the whole table, so a
    page showed whichever tenant happened to have run last. A caller with no company resolves
    to no row rather than to everyone's — a tenancy boundary fails closed.
    """
    if organization_id is None:
        return None
    rows = await _rows(session, """
        SELECT run_id::text, started_at, finished_at, reports_read, assets_covered,
               unconverted, corroborated_anomalies, warranted_findings, poorly_graded,
               unanswerable, scope
          FROM plenum_cafm.inspection_read_runs
         WHERE organization_id = CAST(:org AS uuid)
         ORDER BY started_at DESC LIMIT 1""", {"org": str(organization_id)}, "last_inspection_read")
    if not rows:
        return None
    r = rows[0]
    for k in ("started_at", "finished_at"):
        r[k] = r[k].isoformat() if r[k] else None
    return r
