"""What a meter's findings add up to — which is not the sum of them.

Several detectors read the same meter over the same period, and each one prices the whole
excess it can see. They are different lenses, not different faults: ``weekend_spike`` looks at
weekends, ``nonocc_spike`` at every unoccupied hour, and a weekend IS an unoccupied hour, so
the second already contains the first. Adding them counts the same kilowatt-hour twice.

Bishopsgate Tower on plenum_agent is what this was written against. One electricity meter
carried four priced findings — post-works regression 1,131,373 kWh, non-occupancy spike
561,563, weather residual 191,622, weekend spike 163,710 — and the page added them to
2,048,268 kWh on a meter whose building reads 205 kWh/m2 against a reference of 212. A
building three per cent UNDER its own benchmark cannot also be wasting two gigawatt-hours a
year, and the two figures sat two inches apart on the same screen.

So this reports the largest single finding as the number, lists the rest as corroboration, and
says plainly what the naive sum would have been. Three reasons for choosing the largest rather
than attempting a true union:

  * It is defensible. The largest finding is a real measurement made by one rule over one
    window; nobody has to accept an apportionment nobody can check.
  * It is conservative in the right direction. It understates rather than overstates, and an
    overstated waste figure is the one that gets a building manager into trouble.
  * A true union needs the half-hourly readings each rule matched, intersected per interval.
    The rules do not record which intervals they fired on — and until window_start is
    populated they could not be intersected even in principle.

The sum is still reported, as ``if_added``, because a number that quietly drops from one
figure to another with no explanation is its own kind of dishonesty.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .detection_coverage import RULES

log = structlog.get_logger(__name__)

# The label and the sentence behind every rule, keyed by the type the detector writes.
_RULE: dict[str, dict[str, str]] = {
    r[0]: {"tag": r[1], "label": r[2], "tier": r[3], "reason": r[4], "needs": r[5]} for r in RULES
}

# Rules that are definitionally contained in another: every hour the first can fire on is an
# hour the second also covers. Only relationships true by construction go here — anything
# weaker is reported as "may overlap", which is the honest word for it.
CONTAINED_IN: dict[str, str] = {
    "weekend_spike": "nonocc_spike",
}

_PRICED = "amount"


def _rule_of(kind: str | None) -> dict[str, str]:
    k = str(kind or "")
    return _RULE.get(k) or {
        "tag": k,
        "label": k.replace("_", " ").strip() or "unnamed rule",
        "tier": "unknown",
        "reason": "No description on file for this rule.",
        "needs": "",
    }


def _scope(building_ids: list[UUID] | None, col: str) -> tuple[str, dict[str, Any]]:
    """None means unrestricted; [] means allocated to nothing and must match no row."""
    if building_ids is None:
        return "", {}
    if not building_ids:
        return " AND FALSE", {}
    return f" AND {col}::text = ANY(CAST(:bids AS text[]))", {"bids": [str(b) for b in building_ids]}


def combine(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """The headline for one meter, and the arithmetic behind refusing to add.

    Pure, so the rule can be tested without a database.
    """
    priced = [f for f in findings
              if isinstance(f.get(_PRICED), (int, float)) and float(f[_PRICED]) > 0]
    if_added = round(sum(float(f[_PRICED]) for f in priced), 2)
    kwh_if_added = round(sum(float(f.get("kwh") or 0) for f in priced), 2)

    head = max(priced, key=lambda f: float(f[_PRICED])) if priced else None

    # What is definitely counted more than once, and what merely might be.
    kinds = {f["rule"] for f in priced}
    contained = [
        {"rule": k, "inside": CONTAINED_IN[k],
         "amount": next((float(f[_PRICED]) for f in priced if f["rule"] == k), 0.0),
         "why": ("every hour " + _rule_of(k)["label"].lower() + " can fire on is also an hour "
                 + _rule_of(CONTAINED_IN[k])["label"].lower() + " covers")}
        for k in sorted(kinds) if CONTAINED_IN.get(k) in kinds
    ]
    certain = round(sum(c["amount"] for c in contained), 2)

    may = sorted(kinds - {head["rule"]} - {c["rule"] for c in contained}) if head else []

    return {
        "headline": None if head is None else {
            "rule": head["rule"], "label": head["label"], "reason": head["reason"],
            "amount": float(head[_PRICED]), "kwh": float(head.get("kwh") or 0),
            "currency": head.get("currency"),
        },
        "if_added": if_added,
        "kwh_if_added": kwh_if_added,
        # The part of if_added that is provably the same energy counted twice.
        "double_counted_at_least": certain,
        "contained": contained,
        "may_overlap": may,
        "priced_rules": len(priced),
        "unpriced_rules": len(findings) - len(priced),
    }


async def rollup(
    session: AsyncSession,
    *,
    building_ids: list[UUID] | None = None,
    organization_id: UUID | None = None,
    status: str | None = "open",
) -> dict[str, Any]:
    """Every building's findings, grouped by meter, with a headline that is not a sum."""
    clause, params = _scope(building_ids, "a.building_id")
    if organization_id is not None:
        clause += " AND a.organization_id = CAST(:org AS uuid)"
        params["org"] = str(organization_id)
    if status:
        clause += " AND a.status = :status"
        params["status"] = status

    sql = f"""
        SELECT a.id::text            AS id,
               a.building_id::text   AS building_id,
               b.name                AS building,
               a.meter_id::text      AS meter_id,
               m.mpan_mprn           AS meter_ref,
               m.meter_type          AS meter_type,
               a.asset_id::text      AS asset_id,
               a.anomaly_type        AS rule,
               a.status              AS status,
               a.metric_pct::float   AS metric_pct,
               a.financial_gbp::float         AS amount,
               a.annualised_excess_kwh::float AS kwh,
               a.currency            AS currency,
               a.detected_at         AS detected_at,
               a.window_start        AS window_start,
               a.window_end          AS window_end
          FROM plenum_cafm.energy_anomalies a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
          LEFT JOIN plenum_cafm.meters    m ON m.meter_id    = a.meter_id
         WHERE TRUE{clause}
         ORDER BY b.name, m.mpan_mprn, a.financial_gbp DESC NULLS LAST
    """
    rows = [dict(r) for r in (await session.execute(text(sql), params)).mappings().all()]

    by_building: dict[str, dict[str, Any]] = {}
    for r in rows:
        bid = r["building_id"] or "__none"
        b = by_building.setdefault(bid, {
            "building_id": r["building_id"],
            "building": r["building"] or "Not in the register",
            "currency": r.get("currency"),
            "meters": {},
        })
        mid = r["meter_id"] or "__none"
        m = b["meters"].setdefault(mid, {
            "meter_id": r["meter_id"],
            "meter": (r["meter_ref"] or ("no meter on this finding" if not r["meter_id"]
                                         else str(r["meter_id"])[:8])),
            "meter_type": r["meter_type"],
            "findings": [],
        })
        rl = _rule_of(r["rule"])
        m["findings"].append({
            "id": r["id"], "rule": r["rule"], "label": rl["label"], "reason": rl["reason"],
            "needs": rl["needs"], "tier": rl["tier"],
            "amount": r["amount"], "kwh": r["kwh"], "currency": r["currency"],
            "status": r["status"], "metric_pct": r["metric_pct"],
            "detected_at": r["detected_at"].isoformat() if r["detected_at"] else None,
            # Null on every row on both databases today. Without it no two findings can be
            # intersected, which is why the headline is a choice and not a union.
            "window_start": r["window_start"].isoformat() if r["window_start"] else None,
            "window_end": r["window_end"].isoformat() if r["window_end"] else None,
            "window_known": bool(r["window_start"] and r["window_end"]),
        })

    out_buildings = []
    for b in by_building.values():
        meters = [{**m, **combine(m["findings"])} for m in b["meters"].values()]
        meters.sort(key=lambda x: (x.get("headline") or {}).get("amount") or 0, reverse=True)

        # The building takes the largest meter's headline, for the same reason a meter takes
        # the largest rule's: two meters on one building can be reading the same supply
        # through a parent and a sub-meter, and adding those is the same mistake one level up.
        heads = [m["headline"] for m in meters if m.get("headline")]
        top = max(heads, key=lambda h: h["amount"]) if heads else None
        out_buildings.append({
            "building_id": b["building_id"], "building": b["building"],
            "currency": b["currency"],
            "anomalies": sum(len(m["findings"]) for m in meters),
            "meters_affected": len([m for m in meters if m.get("headline")]),
            "headline": top,
            "if_added": round(sum(m["if_added"] for m in meters), 2),
            "kwh_if_added": round(sum(m["kwh_if_added"] for m in meters), 2),
            "double_counted_at_least": round(sum(m["double_counted_at_least"] for m in meters), 2),
            "meters": meters,
        })
    out_buildings.sort(key=lambda x: (x.get("headline") or {}).get("amount") or 0, reverse=True)

    return {
        "ok": True,
        "method": "rule:largest-single-finding/v1",
        "note": ("Findings on one meter are different readings of the same consumption, not "
                 "separate faults, so they are not added. The headline is the largest single "
                 "finding; if_added is what adding them would have given."),
        "buildings": out_buildings,
        "count": len(out_buildings),
    }
