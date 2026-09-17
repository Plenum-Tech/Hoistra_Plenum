"""Detection coverage: which anomaly rules can run, did run, and fired — per building, per
asset, from the scan itself rather than from a guess about the data route.

The Energy page's "Detection rules" panel used to count coverage client-side from a meter's
route string. This asks the detectors. Every active meter in scope is scanned with
``persist=False`` (nothing written, nothing queued), every chiller with a design figure is
assessed, and the result is one matrix: for each building and each of the thirteen rules,
``fired`` (with the figure), ``clear`` (ran, nothing found), or ``skipped`` with the reason
the scan gave — no capacity on the meter, no BMS trend in the last week, no closed work
order, fewer than twelve complete months. The reason is the fix.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import anomalies as anom_svc
from . import chiller as chiller_svc

log = get_logger(__name__)

#: The thirteen rules, in the order the shell lists them: id, short id, label, whether it is
#: one of the three core rules or an added one, what it tests, and the data route it needs.
#:
#: The last two used to be copy on the page. They belong here, next to the detector that
#: implements them, because a rule whose description lives somewhere else drifts from what it
#: actually does and nobody notices.
RULES: list[tuple[str, str, str, str, str, str]] = [
    ("nonocc_spike", "nonocc", "Non-occupancy spike", "core",
     "consumption in unoccupied hours > 30% of the occupied-hours average",
     "needs half-hourly or interval meter"),
    ("asset_spike", "spike", "Single-asset spike", "core",
     "one sub-metered asset > 2\u03c3 above its own 4-week profile",
     "needs sub-meter or BMS trend"),
    ("baseline_drift", "drift", "Baseline drift", "core",
     "week-on-week baseline rising 3 weeks running with no occupancy change",
     "needs weekly totals"),
    ("schedule_mismatch", "schedule", "Schedule mismatch", "added",
     "plant start or stop more than 45 min outside the occupancy calendar",
     "needs interval meter + occupancy calendar"),
    ("baseload_creep", "baseload", "Baseload creep", "added",
     "overnight minimum rising week on week while daytime is flat",
     "needs half-hourly or interval meter"),
    ("weekend_spike", "calendar", "Calendar rule", "added",
     "weekend or public-holiday profile within 15% of a weekday",
     "needs daily totals"),
    ("weather_residual", "weather", "Weather-normalised residual", "added",
     "CUSUM on degree-day regression residuals breaches the control limit",
     "needs monthly totals + local degree days"),
    ("peak_excursion", "peak", "Peak demand excursion", "added",
     "kVA or kW peak above agreed capacity or prior-year max",
     "needs half-hourly or interval meter"),
    ("simultaneous_heating_cooling", "fight", "Simultaneous heating and cooling", "added",
     "heating and cooling both calling in the same zone for > 30 min",
     "needs BMS trend"),
    ("chiller_efficiency", "cop", "Chiller efficiency", "added",
     "kW per RT more than 15% above design at matched ambient",
     "needs BMS trend or cooling sub-meter"),
    ("post_works_regression", "regress", "Post-works regression", "added",
     "consumption back to pre-fix level within 30 days of a closed work order",
     "needs meter + work order record"),
    ("data_quality", "dataq", "Data-quality anomaly", "added",
     "gaps, flatlines or estimated reads in the feed \u2014 scored as a data fault, never a "
     "building fault",
     "needs any feed"),
    ("tou_misalignment", "tou", "Time-of-use misalignment", "added",
     "shiftable load sitting in the peak price band",
     "needs interval meter + tariff bands"),
]
RULE_IDS = [r[0] for r in RULES]
SETTLED = ("resolved", "closed", "dismissed")


def _merge(cell: dict[str, Any] | None, outcome: str, **extra: Any) -> dict[str, Any]:
    """Combine one meter's outcome into the building's cell: fired beats clear beats skipped."""
    rank = {"fired": 3, "clear": 2, "skipped": 1}
    if cell is None or rank[outcome] > rank[cell["status"]]:
        return {"status": outcome, **extra}
    if outcome == "fired" and cell["status"] == "fired":
        cell["hits"] = (cell.get("hits") or 0) + (extra.get("hits") or 0)
    return cell


def _rule_catalogue(armed_by: dict[str, int] | None = None,
                    buildings: int = 0) -> list[dict[str, Any]]:
    """The thirteen rules as the panel lists them, armed or not.

    Returned whether or not anything could be scanned, because the list of rules is a constant
    and an empty panel says "there are no rules" when the truth is "no building here has the
    data route any of them needs" — which is the more useful sentence, and the one the panel's
    own footnote promises.
    """
    return [{
        "rule": rid, "id": short, "label": label, "tag": tag,
        "description": desc, "needs": needs,
        "armed": (armed_by or {}).get(rid, 0), "buildings_in_scope": buildings,
        "fired": 0, "clear": 0, "skipped": buildings - (armed_by or {}).get(rid, 0),
        "skipped_reasons": {}, "buildings_fired": [],
    } for rid, short, label, tag, desc, needs in RULES]


async def coverage(
    session: AsyncSession, *, building_ids: list[UUID] | None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Which rules can run, did run and fired, per building in scope.

    ``building_ids`` follows the same convention as every other read here: ``None`` is
    unrestricted and resolves to every building on record, an empty list is a caller allocated
    to nothing. Conflating the two meant an administrator — who is unrestricted, and therefore
    passes None — got an empty panel.
    """
    if building_ids is None:
        building_ids = (await session.execute(text(
            "SELECT building_id FROM plenum_cafm.buildings WHERE building_id IS NOT NULL"
        ))).scalars().all()
    if not building_ids:
        return {"ok": True, "buildings": [], "rules": _rule_catalogue(),
                "summary": {"buildings": 0},
                "note": "no buildings in scope, so no rule could arm on one"}
    ids = [str(b) for b in building_ids]
    blds = (await session.execute(text("""
        SELECT b.building_id::text AS building_id, b.name,
               coalesce(s.country_code, b.raw_metadata->>'country_code') AS country_code
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
         WHERE b.building_id = ANY(CAST(:ids AS uuid[])) ORDER BY b.name"""), {"ids": ids})).mappings().all()
    meters = (await session.execute(text("""
        SELECT id::text, building_id::text AS building_id, meter_type, is_sub_meter, asset_id::text AS asset_id,
               coalesce(raw_metadata->>'asset_label', mpan, mprn, id::text) AS label,
               coalesce(raw_metadata->>'simulate','false') = 'true' AS simulated
          FROM plenum_cafm.energy_meters WHERE active AND building_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY is_sub_meter, meter_type"""), {"ids": ids})).mappings().all()
    chillers = (await session.execute(text("""
        SELECT asset_id::text, building_id::text FROM plenum_cafm.chiller_design_specs
         WHERE building_id = ANY(CAST(:ids AS uuid[]))"""), {"ids": ids})).mappings().all()
    open_rows = (await session.execute(text("""
        SELECT building_id::text AS building_id, anomaly_type, count(*) AS n
          FROM plenum_cafm.energy_anomalies
         WHERE building_id = ANY(CAST(:ids AS uuid[])) AND status NOT IN ('resolved','closed','dismissed')
         GROUP BY 1, 2"""), {"ids": ids})).mappings().all()
    open_by: dict[str, dict[str, int]] = {}
    for r in open_rows:
        open_by.setdefault(r["building_id"], {})[r["anomaly_type"]] = int(r["n"])

    cells: dict[str, dict[str, dict[str, Any]]] = {b["building_id"]: {} for b in blds}
    meter_rows: dict[str, list[dict[str, Any]]] = {b["building_id"]: [] for b in blds}
    for m in meters:
        # Each scan in its own savepoint: one meter's failed statement must not abort the
        # transaction for every meter after it (which is how one bad row once turned the
        # whole report into "skipped").
        try:
            async with session.begin_nested():
                r = await anom_svc.scan_meter_anomalies(session, meter_id=UUID(m["id"]), organization_id=organization_id,
                                                        persist=False)
        except Exception as exc:  # noqa: BLE001 — one meter's failure is a row, not the report
            log.warning("detection_coverage.scan_failed", meter_id=m["id"], error=str(exc)[:400])
            r = {"ok": False, "anomalies": [], "skipped": {"all": str(exc).splitlines()[0][:160]}, "rules_run": 0}
        fired: dict[str, dict[str, Any]] = {}
        for h in r.get("anomalies") or []:
            fired.setdefault(h["anomaly_type"], {"hits": 0, "metric_pct": h.get("metric_pct"),
                                                 "financial": h.get("financial_gbp"), "currency": h.get("currency")})
            fired[h["anomaly_type"]]["hits"] += 1
        skipped = r.get("skipped") or {}
        ran = [rid for rid in RULE_IDS if rid not in skipped and rid != "chiller_efficiency"
               and (rid != "asset_spike" or m["is_sub_meter"])]
        if "all" in skipped:
            ran = []
        meter_rows[m["building_id"]].append({
            "meter_id": m["id"], "label": m["label"], "fuel": m["meter_type"], "sub_meter": bool(m["is_sub_meter"]),
            "asset_id": m["asset_id"], "simulated": bool(m["simulated"]),
            "fired": sorted(fired), "ran": len(ran), "skipped": skipped,
        })
        cell = cells.setdefault(m["building_id"], {})
        for rid in RULE_IDS:
            if rid == "chiller_efficiency":
                continue
            if rid in fired:
                cell[rid] = _merge(cell.get(rid), "fired", hits=fired[rid]["hits"], metric_pct=fired[rid]["metric_pct"],
                                   financial=fired[rid]["financial"], currency=fired[rid]["currency"], meter=m["label"])
            elif rid in ran:
                cell[rid] = _merge(cell.get(rid), "clear")
            else:
                reason = skipped.get(rid) or skipped.get("all") or (
                    "runs on sub-meters only" if rid == "asset_spike" else "not armed on this meter")
                cell[rid] = _merge(cell.get(rid), "skipped", reason=reason)

    chill_by: dict[str, list[str]] = {}
    for c in chillers:
        chill_by.setdefault(c["building_id"], []).append(c["asset_id"])
    for bid in cells:
        assets = chill_by.get(bid) or []
        if not assets:
            cells[bid]["chiller_efficiency"] = {"status": "skipped", "reason": "no chiller design figures on file"}
            continue
        outcome: dict[str, Any] | None = None
        for aid in assets:
            try:
                async with session.begin_nested():
                    a = await chiller_svc.assess(session, asset_id=UUID(aid))
            except Exception as exc:  # noqa: BLE001
                log.warning("detection_coverage.chiller_failed", asset_id=aid, error=str(exc)[:400])
                a = {"ok": False, "error": str(exc).splitlines()[0][:120]}
            if not a.get("ok"):
                outcome = _merge(outcome, "skipped", reason=str(a.get("error") or "not assessed"))
            elif a.get("breach"):
                outcome = _merge(outcome, "fired", hits=1, metric_pct=a.get("deviation_pct"),
                                 financial=a.get("financial_gbp"), currency=a.get("currency"), asset=aid)
            else:
                outcome = _merge(outcome, "clear", metric_pct=a.get("deviation_pct"))
        cells[bid]["chiller_efficiency"] = outcome or {"status": "skipped", "reason": "not assessed"}

    buildings_out = []
    for b in blds:
        bid = b["building_id"]
        rules = cells.get(bid, {})
        buildings_out.append({
            "building_id": bid, "name": b["name"], "country_code": b["country_code"],
            "meters": len([m for m in meter_rows[bid] if not m["sub_meter"]]),
            "sub_meters": len([m for m in meter_rows[bid] if m["sub_meter"]]),
            "chillers": len(chill_by.get(bid) or []),
            "open_anomalies": sum(open_by.get(bid, {}).values()),
            "open_by_type": open_by.get(bid, {}),
            "rules": {rid: rules.get(rid, {"status": "skipped", "reason": "no active meter"}) for rid in RULE_IDS},
            "fired": sorted(rid for rid, c in rules.items() if c["status"] == "fired"),
            "armed": sum(1 for c in rules.values() if c["status"] in ("fired", "clear")),
            "meter_rows": meter_rows[bid],
        })

    rules_out = []
    for rid, short, label, tag, desc, needs in RULES:
        statuses = [b["rules"][rid]["status"] for b in buildings_out]
        reasons: dict[str, int] = {}
        for b in buildings_out:
            c = b["rules"][rid]
            if c["status"] == "skipped":
                reasons[c.get("reason") or "?"] = reasons.get(c.get("reason") or "?", 0) + 1
        rules_out.append({
            "rule": rid, "id": short, "label": label, "tag": tag,
            "description": desc, "needs": needs,
            "buildings_in_scope": len(buildings_out),
            "armed": sum(1 for s in statuses if s in ("fired", "clear")),
            "fired": sum(1 for s in statuses if s == "fired"),
            "clear": sum(1 for s in statuses if s == "clear"),
            "skipped": sum(1 for s in statuses if s == "skipped"),
            "skipped_reasons": reasons,
            "buildings_fired": [b["name"] for b in buildings_out if b["rules"][rid]["status"] == "fired"],
        })
    return {
        "ok": True,
        "buildings": buildings_out,
        "rules": rules_out,
        "summary": {
            "buildings": len(buildings_out),
            "meters": len(meters), "sub_meters": sum(1 for m in meters if m["is_sub_meter"]),
            "chillers": len(chillers),
            "rules_firing_somewhere": sum(1 for r in rules_out if r["fired"]),
            "rules_armed_somewhere": sum(1 for r in rules_out if r["armed"]),
            "open_anomalies": sum(b["open_anomalies"] for b in buildings_out),
        },
    }
