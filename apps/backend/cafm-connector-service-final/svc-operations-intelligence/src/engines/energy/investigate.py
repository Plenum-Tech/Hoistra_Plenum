"""Investigate one asset: walk the sources, state the evidence, propose actions, write nothing.

This is what the Investigate button opens. It answers "why is this asset costing what it is,
and what should I do about it" by reading the sources that would settle the question and saying
what each one gave back — including the ones that gave back nothing, because a record the
contract requires and nobody filed is not a gap in the investigation, it is a finding in it.

Three rules run through the whole thing.

**Absence is evidence, and it is the strongest kind.** A cooling-tower PPM that closed with no
report and no treatment log is a fact with no inference in it at all. Findings like that carry
full confidence; the ones that reason from a measurement to a cause do not, and the difference
is declared rather than blurred.

**Confidence is a property of the rule, not of the run.** Each rule states how directly its
evidence supports its statement — a measured gap with the obvious confounder ruled out is not
the same as a trend consistent with two or three causes — and that number is fixed where the
rule is written, so two investigations that found the same thing say the same thing about it.
Nothing here computes a confidence from a model.

**Nothing is written.** The actions come back as proposals with the parameters they would be
raised with, and the caller raises them through the endpoints that already exist. The phrase
on the page — actions ready, nothing has been written yet — is literally true of this module:
it holds no INSERT, no UPDATE, and no queue write.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import asset_intelligence as ai

log = get_logger(__name__)

#: The rule set that produced an investigation, named in the response.
METHOD = "rule:asset-investigation/v1"

#: How many weeks of readings an investigation looks back over.
WINDOW_WEEKS = 8

#: What a confidence figure means here. These are fixed per kind of evidence, not computed:
#: a reading missing from a register is a fact, and a cause inferred from a trend is not.
CONF_RECORD_ABSENT = 1.0      # the record is not there; nothing is inferred
CONF_MEASURED_TOTAL = 1.0     # a total that is simply what it is, even if it explains nothing
CONF_MEASURED_GAP = 0.92      # measured against a design figure, with the confounder ruled out
CONF_TREND_TO_CAUSE = 0.85    # a measured trend whose signature fits a small set of causes
CONF_CORROBORATION = 0.80     # consistent with the finding, but could be other things too

#: A chiller more than this far above its design efficiency is running badly enough to name.
KW_PER_RT_TOLERANCE = 0.05

#: Degree days within this of last year are "flat" — weather is not the explanation.
CDD_FLAT_PCT = 10.0

#: Two halves of a window are comparable when their mean ambient is within this. A hotter
#: second half explains a worse second half without anything being wrong with the plant.
AMBIENT_MATCH_C = 2.0

#: A drift in kW/RT bigger than this, at matched ambient, is worth naming.
KW_PER_RT_DRIFT = 0.02


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


async def _rows(session: AsyncSession, sql: str, params: dict, what: str) -> list[dict]:
    """One source, in its own savepoint. A source this database shapes differently comes back
    empty and is reported as unreadable — it does not take the investigation down with it."""
    try:
        async with session.begin_nested():
            return [dict(r) for r in
                    (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001
        log.warning("investigate.source_failed", source=what, error=str(exc)[:220])
        return []


def _source(name: str, question: str, status: str, badge: str, **detail) -> dict[str, Any]:
    """One line of the walk: what was asked of a source and what it gave back."""
    return {"source": name, "question": question, "status": status, "badge": badge, **detail}


# ── the walk ─────────────────────────────────────────────────────────────────────────

async def _walk_efficiency(session, asset, since) -> dict[str, Any]:
    """kW per RT against design, and whether it is drifting at comparable ambient.

    Condenser water approach would be the cleaner signal for tower fouling, and it is not
    computed here because the columns that would carry it — the chilled-water supply and
    return temperatures — hold nothing on either database. Reporting an approach figure from
    an empty column would be inventing a measurement, so what is measured instead is the
    efficiency drift across the window **at matched ambient**: the same plant, the same
    outside conditions, getting worse. The response says which of the two it had.
    """
    aid = asset["asset_id"]
    design = await _rows(session, """
        SELECT design_kw_per_rt, design_capacity_rt, design_ambient_c, source
          FROM plenum_cafm.chiller_design_specs WHERE asset_id::text = :aid LIMIT 1""",
        {"aid": aid}, "chiller_design")
    mid = since + (datetime.now(timezone.utc) - since) / 2
    reads = await _rows(session, """
        SELECT count(*) AS points,
               avg(CASE WHEN cooling_load_rt > 0 THEN kw_input / cooling_load_rt END) AS kw_per_rt,
               count(chw_supply_c) AS approach_points,
               avg(ambient_c) AS ambient,
               -- The window split, each half kept with its own ambient so the comparison can
               -- be refused if the two halves were not run in comparable conditions.
               avg(CASE WHEN reading_at <  :mid AND cooling_load_rt > 0
                        THEN kw_input / cooling_load_rt END) AS early_kw_rt,
               avg(CASE WHEN reading_at >= :mid AND cooling_load_rt > 0
                        THEN kw_input / cooling_load_rt END) AS late_kw_rt,
               avg(CASE WHEN reading_at <  :mid THEN ambient_c END) AS early_ambient,
               avg(CASE WHEN reading_at >= :mid THEN ambient_c END) AS late_ambient,
               min(reading_at) AS first_at, max(reading_at) AS last_at
          FROM plenum_cafm.chiller_performance_readings
         WHERE asset_id::text = :aid AND reading_at >= :since""",
        {"aid": aid, "since": since, "mid": mid}, "chiller_readings")
    r = reads[0] if reads else {}
    points = int(r.get("points") or 0)
    actual = _num(r.get("kw_per_rt"))
    spec = _num(design[0]["design_kw_per_rt"]) if design else None

    if not points:
        return _source("bms_trend", f"{asset['asset_name']} kW per RT against design",
                       "not_found", "no readings",
                       detail="no performance reading is on record for this asset")

    early, late = _num(r.get("early_kw_rt")), _num(r.get("late_kw_rt"))
    ea, la = _num(r.get("early_ambient")), _num(r.get("late_ambient"))
    # Only a drift measured at comparable ambient says anything about the plant. A hotter
    # second half explains a worse second half on its own.
    comparable = (ea is not None and la is not None and abs(la - ea) <= AMBIENT_MATCH_C)
    drift = (round(late - early, 3)
             if early is not None and late is not None and comparable else None)
    approach_points = int(r.get("approach_points") or 0)

    return _source(
        "bms_trend", f"{asset['asset_name']} kW per RT against design, and its drift",
        "found", f"{points:,} points",
        points=points, kw_per_rt=round(actual, 3) if actual else None,
        design_kw_per_rt=spec,
        over_design=(actual is not None and spec is not None
                     and actual > spec + KW_PER_RT_TOLERANCE),
        kw_per_rt_drift=drift,
        drift_at_matched_ambient=comparable,
        early_kw_per_rt=round(early, 3) if early is not None else None,
        late_kw_per_rt=round(late, 3) if late is not None else None,
        ambient_early_c=round(ea, 1) if ea is not None else None,
        ambient_late_c=round(la, 1) if la is not None else None,
        condenser_approach=("not on record — the chilled-water supply and return columns are "
                            "empty here, so approach temperature cannot be computed"
                            if not approach_points else None),
        first_at=_iso(r.get("first_at")), last_at=_iso(r.get("last_at")))


async def _walk_meter(session, asset, since) -> dict[str, Any]:
    """Whether the plant this asset sits in is sub-metered, and what it drew."""
    rows = await _rows(session, """
        SELECT count(DISTINCT m.id) AS meters,
               bool_or(m.is_sub_meter) AS sub_metered,
               sum(r.consumption_kwh) AS kwh
          FROM plenum_cafm.energy_meters m
          LEFT JOIN plenum_cafm.meter_readings r
                 ON r.meter_id = m.id AND r.reading_at >= :since
         WHERE m.active AND (m.asset_id::text = :aid OR m.building_id = :bid)""",
        {"aid": asset["asset_id"], "bid": asset["building_id"], "since": since}, "meters")
    r = rows[0] if rows else {}
    meters = int(r.get("meters") or 0)
    if not meters:
        return _source("meter_reading", f"sub-metered plant, {WINDOW_WEEKS} weeks",
                       "not_found", "no meter",
                       detail="no active meter covers this asset or its building")
    sub = bool(r.get("sub_metered"))
    return _source("meter_reading", f"sub-metered plant, {WINDOW_WEEKS} weeks",
                   "found" if sub else "partial",
                   "sub-metered" if sub else "building-level only",
                   meters=meters, sub_metered=sub, kwh=_num(r.get("kwh")))


async def _walk_bill(session, asset, since) -> dict[str, Any]:
    """Billed consumption against the same period last year.

    There is no utility-bill register on this platform, so this is answered from metered
    consumption over the billing period instead — and says so, because a bill and a meter
    are different records and one standing in for the other has to be visible.
    """
    rows = await _rows(session, """
        SELECT sum(CASE WHEN r.reading_at >= :since THEN r.consumption_kwh END) AS now_kwh,
               sum(CASE WHEN r.reading_at >= :since - interval '1 year'
                         AND r.reading_at <  :since - interval '1 year' + (now() - :since)
                        THEN r.consumption_kwh END) AS last_year_kwh
          FROM plenum_cafm.energy_meters m
          JOIN plenum_cafm.meter_readings r ON r.meter_id = m.id
         WHERE m.active AND m.building_id = :bid""",
        {"bid": asset["building_id"], "since": since}, "billing")
    r = rows[0] if rows else {}
    now_kwh, last_kwh = _num(r.get("now_kwh")), _num(r.get("last_year_kwh"))
    if not now_kwh or not last_kwh:
        return _source("utility_bill", "billing-period consumption vs same month last year",
                       "not_found", "no comparable period",
                       detail=("no utility-bill register exists here, and metered consumption "
                               "does not cover the same period last year"))
    change = round((now_kwh - last_kwh) / last_kwh * 100, 1)
    return _source("utility_bill", "billing-period consumption vs same month last year",
                   "partial", f"{change:+.0f}%",
                   change_pct=change, now_kwh=round(now_kwh), last_year_kwh=round(last_kwh),
                   detail=("from metered consumption — there is no utility-bill register on "
                           "this platform, so the meter stands in for the bill"))


async def _walk_weather(session, asset, since) -> dict[str, Any]:
    """Cooling degree days against last year: is the weather the explanation?"""
    rows = await _rows(session, """
        SELECT sum(CASE WHEN month >= :since_m THEN cdd END) AS now_cdd,
               sum(CASE WHEN month >= (:since_m::date - interval '1 year')
                         AND month <  (:since_m::date - interval '1 year')
                                      + age(current_date, :since_m::date) THEN cdd END)
                   AS last_cdd
          FROM plenum_cafm.weather_degree_days WHERE building_id = :bid""",
        {"bid": asset["building_id"], "since_m": since.date().replace(day=1)}, "weather")
    r = rows[0] if rows else {}
    now_cdd, last_cdd = _num(r.get("now_cdd")), _num(r.get("last_cdd"))
    if not now_cdd or not last_cdd:
        return _source("weather", "cooling degree days vs last year", "not_found",
                       "no degree days",
                       detail="no degree-day record covers this building and period")
    change = round((now_cdd - last_cdd) / last_cdd * 100, 1)
    flat = abs(change) <= CDD_FLAT_PCT
    return _source("weather", "cooling degree days vs last year", "found",
                   "flat" if flat else f"{change:+.0f}%",
                   change_pct=change, flat=flat, now_cdd=round(now_cdd),
                   last_year_cdd=round(last_cdd))


async def _walk_work_orders(session, asset, since) -> dict[str, Any]:
    """The planned work around this asset, and whether a report came off it."""
    rows = await _rows(session, """
        SELECT coalesce(w.wo_code, w.id::text) AS wo_code, w.status,
               coalesce(w.completed_at, w.closed_at, w.created_at) AS closed_at,
               coalesce(v.vendor_name, w.assigned_vendor::text) AS vendor,
               coalesce(w.title, w.issue_description) AS title,
               EXISTS (SELECT 1 FROM plenum_cafm.inspections i
                        WHERE i.asset_id::text = :aid
                          AND (i.work_order_id = w.id::text
                               OR i.work_order_id = coalesce(w.wo_code, ''))) AS has_report
          FROM plenum_cafm.work_orders w
          LEFT JOIN plenum_cafm.vendors v ON v.id::text = w.assigned_vendor::text
         WHERE w.asset_id::text = :aid
         ORDER BY coalesce(w.completed_at, w.closed_at, w.created_at) DESC NULLS LAST
         LIMIT 10""", {"aid": asset["asset_id"]}, "work_orders")
    if not rows:
        return _source("work_order", "planned maintenance on this asset", "not_found",
                       "none on record",
                       detail="no work order names this asset")
    closed_no_report = [r for r in rows
                        if not r["has_report"]
                        and str(r.get("status") or "").lower() in
                        ("completed", "closed", "complete", "done")]
    latest = rows[0]
    return _source(
        "work_order", f"planned maintenance by {latest.get('vendor') or 'the vendor'}",
        "partial" if closed_no_report else "found",
        "closed, no report" if closed_no_report else f"{len(rows)} on record",
        orders=len(rows), closed_without_report=len(closed_no_report),
        latest={"wo_code": latest["wo_code"], "status": latest.get("status"),
                "vendor": latest.get("vendor"), "closed_at": _iso(latest.get("closed_at")),
                "title": latest.get("title"), "report_on_file": bool(latest["has_report"])})


async def _walk_documents(session, asset, since) -> dict[str, Any]:
    """The documents that should exist for this asset, and whether any do."""
    rows = await _rows(session, """
        SELECT count(*) AS n FROM plenum_cafm.asset_documents WHERE asset_id::text = :aid""",
        {"aid": asset["asset_id"]}, "asset_documents")
    n = int(rows[0]["n"]) if rows else 0
    if n:
        return _source("document", "records filed against this asset", "found",
                       f"{n} on file", documents=n)
    return _source("document", "records filed against this asset — service and treatment logs",
                   "not_found", "not found", documents=0,
                   detail=("nothing is filed against this asset. Where a contract requires a "
                           "log, its absence is a finding rather than a gap in the search."))


# ── the evidence ─────────────────────────────────────────────────────────────────────

def _evidence(walk: dict[str, dict], asset: dict) -> list[dict[str, Any]]:
    """The findings the walk supports, each naming its sources and how far it reasons.

    Nothing here is generated. Each rule is a stated condition over what the sources returned,
    and it either fires or it does not.
    """
    out: list[dict[str, Any]] = []
    eff, wx = walk.get("bms_trend", {}), walk.get("weather", {})
    wo, doc = walk.get("work_order", {}), walk.get("document", {})
    bill = walk.get("utility_bill", {})

    # 1. Running above design, with the weather ruled out.
    if eff.get("over_design") and eff.get("kw_per_rt") and eff.get("design_kw_per_rt"):
        weather_ruled_out = wx.get("flat") is True
        out.append({
            "statement": (
                f"{asset['asset_name']} is running at {eff['kw_per_rt']:.2f} kW/RT against "
                f"{eff['design_kw_per_rt']:.2f} design"
                + (", with cooling degree days flat on last year — efficiency loss, not weather."
                   if weather_ruled_out else
                   ". Degree days are not flat, so weather is not ruled out.")),
            "sources": ["bms_trend", "weather"] if weather_ruled_out else ["bms_trend"],
            "confidence": CONF_MEASURED_GAP if weather_ruled_out else CONF_CORROBORATION,
            "kind": "measured gap",
        })

    # 2. Getting worse over the window, with the weather held still.
    drift = eff.get("kw_per_rt_drift")
    if drift is not None and drift > KW_PER_RT_DRIFT:
        out.append({
            "statement": (
                f"Efficiency has drifted from {eff['early_kw_per_rt']:.2f} to "
                f"{eff['late_kw_per_rt']:.2f} kW/RT across the window at comparable ambient "
                f"({eff['ambient_early_c']} °C against {eff['ambient_late_c']} °C) — the "
                f"signature of heat-rejection fouling or a lapse in water treatment."),
            "sources": ["bms_trend"],
            "confidence": CONF_TREND_TO_CAUSE,
            "kind": "trend to cause",
        })
    elif eff.get("condenser_approach"):
        out.append({
            "statement": (
                "Condenser water approach — the cleaner signal for tower fouling — cannot be "
                "read: the chilled-water supply and return columns hold nothing for this "
                "asset, so the cause cannot be narrowed beyond the efficiency loss itself."),
            "sources": ["bms_trend"],
            "confidence": CONF_RECORD_ABSENT,
            "kind": "missing reading",
        })

    # 3. The record that is not there. No inference in this at all.
    if wo.get("closed_without_report") and doc.get("status") == "not_found":
        latest = wo.get("latest") or {}
        when = (latest.get("closed_at") or "")[:10]
        out.append({
            "statement": (
                f"The {latest.get('title') or 'planned visit'} closed"
                + (f" on {when}" if when else "")
                + " with no report, and no service or treatment log is filed against the asset."),
            "sources": ["work_order", "document"],
            "confidence": CONF_RECORD_ABSENT,
            "kind": "missing record",
        })
    elif wo.get("closed_without_report"):
        out.append({
            "statement": (f"{wo['closed_without_report']} closed work orders on this asset "
                          f"have no report on file."),
            "sources": ["work_order"], "confidence": CONF_RECORD_ABSENT,
            "kind": "missing record",
        })

    # 4. The total that confirms without attributing.
    if bill.get("change_pct") is not None and bill["change_pct"] > 0:
        out.append({
            "statement": (
                f"Consumption is {bill['change_pct']:+.0f}% on the same period last year. That "
                f"confirms the cost; it cannot attribute it to an asset, and the readings can."),
            "sources": ["utility_bill"],
            "confidence": CONF_MEASURED_TOTAL,
            "kind": "corroborating total",
        })
    return out


def _conclusion(evidence: list[dict], walk: dict, asset: dict,
                anomaly: dict) -> dict[str, Any]:
    """What the evidence adds up to, what it is costing, and what it rests on."""
    kinds = {e["kind"] for e in evidence}
    inferred = "trend to cause" in kinds or "measured gap" in kinds
    missing = "missing record" in kinds

    if not evidence:
        return {"cause": None,
                "statement": ("Nothing in the sources explains this asset's cost. The readings "
                              "that would settle it are not on record."),
                "rests_on": "no evidence", "confirmation_required": True}

    if missing and "trend to cause" in kinds:
        cause = ("Cooling-tower fouling from a lapse in water treatment, unreported because "
                 "the planned visit closed without a report.")
    elif "measured gap" in kinds:
        cause = f"{asset['asset_name']} is consuming above its design efficiency."
    else:
        cause = "The records this asset should carry are not on file."

    return {
        "cause": cause,
        "cost_to_date": _num(anomaly.get("annual_cost")) and round(
            _num(anomaly["annual_cost"]) * min((anomaly.get("weeks") or 0) / 52.0, 1.0), 0),
        "cost_annualised": _num(anomaly.get("annual_cost")),
        "currency": anomaly.get("currency"),
        "rests_on": "inference" if inferred else "record",
        "confirmation_required": bool(missing or inferred),
        "caveat": (
            "A record the contract requires is missing, so the cause rests on inference. "
            "Confirm with the people who were on site and request the missing document "
            "before anything is claimed." if missing else
            "The cause is inferred from readings rather than read from a record. Confirm "
            "before anything is claimed." if inferred else None),
    }


def _actions(evidence: list[dict], walk: dict, asset: dict) -> list[dict[str, Any]]:
    """What could be raised off this, with the parameters it would be raised with.

    Proposals. This module writes nothing: each action names the endpoint that would carry it
    out and the body it would carry, and the caller raises it if a person approves.
    """
    kinds = {e["kind"] for e in evidence}
    wo = walk.get("work_order", {}) or {}
    vendor = (wo.get("latest") or {}).get("vendor")
    eff = walk.get("bms_trend", {}) or {}
    out: list[dict[str, Any]] = []

    if "trend to cause" in kinds or "measured gap" in kinds:
        out.append({
            "id": "raise_work_order",
            "label": "Raise WO — water treatment and tower inspection",
            "detail": f"P2 · {vendor or 'the contracted vendor'} · at contracted rate",
            "endpoint": "POST /api/work-orders/",
            "body": {"asset": asset["asset_name"], "building_id": asset["building_id"],
                     "request_type": "maintenance", "priority": "P2",
                     "issue_description": ("Water treatment and cooling-tower inspection — "
                                           "condenser approach rising, efficiency above design")},
        })
    if "missing record" in kinds:
        out.append({
            "id": "request_records",
            "label": "Request PPM report and treatment log",
            "detail": f"draft to {vendor or 'the vendor'}",
            "endpoint": "POST /api/work-orders/",
            "body": {"asset": asset["asset_name"], "building_id": asset["building_id"],
                     "request_type": "inspection",
                     "issue_description": ("Request the report and water-treatment log for the "
                                           "closed planned visit — neither is on file")},
        })
    if eff.get("over_design"):
        out.append({
            "id": "resequence",
            "label": "Re-sequence to favour the healthier unit",
            "detail": "BMS change · interim while this one is cleaned",
            "endpoint": None,
            "body": None,
            "note": "a BMS change; this platform proposes it but does not make it",
        })
        target = round((eff.get("design_kw_per_rt") or 0) + KW_PER_RT_TOLERANCE, 2)
        out.append({
            "id": "recheck",
            "label": f"Re-check kW/RT in 10 days",
            "detail": f"back under {target} closes the anomaly",
            "endpoint": "GET /api/energy/chillers/{asset_id}/efficiency",
            "body": None,
        })
    return out


# ── the investigation ────────────────────────────────────────────────────────────────

async def investigate(
    session: AsyncSession, *, asset_id: str, building_ids: list[UUID] | None,
) -> dict[str, Any]:
    """Walk the sources for one asset and say what they support. Writes nothing."""
    detail = await ai.asset_detail(session, asset_id=asset_id, building_ids=building_ids)
    if not detail.get("ok"):
        return {"ok": False, "reason": detail.get("reason", "not_found_or_not_in_scope"),
                "asset_id": str(asset_id)}

    a = detail["asset"]
    asset = {"asset_id": a["id"], "asset_name": a["asset_name"],
             "building_id": a["building_id"], "building": a["building"]}
    since = datetime.now(timezone.utc) - timedelta(weeks=WINDOW_WEEKS)

    walkers = (_walk_efficiency, _walk_meter, _walk_bill, _walk_weather,
               _walk_work_orders, _walk_documents)
    walk_list = [await w(session, asset, since) for w in walkers]
    walk = {w["source"]: w for w in walk_list}

    evidence = _evidence(walk, asset)
    conclusion = _conclusion(evidence, walk, asset, detail.get("anomaly") or {})
    actions = _actions(evidence, walk, asset)

    return {
        "ok": True,
        "asset": {**asset, "vendor": a.get("vendor"), "section": a.get("section")},
        "method": METHOD,
        "plan": (
            f"I will walk {len(walkers)} sources — the readings first, then the maintenance "
            f"record around the asset, then the documents that should exist for it."),
        "sources": walk_list,
        "sources_found": sum(1 for w in walk_list if w["status"] == "found"),
        "sources_missing": [w["source"] for w in walk_list if w["status"] == "not_found"],
        "evidence": evidence,
        "conclusion": conclusion,
        "actions": actions,
        "written": False,
        "note": ("nothing has been written: every action is a proposal naming the endpoint and "
                 "body it would be raised with. Confidence is fixed per rule by how directly "
                 "its evidence supports it, and a missing record carries full confidence "
                 "because nothing is inferred from it."),
    }
