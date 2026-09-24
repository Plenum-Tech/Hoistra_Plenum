"""Sub-meters by floor: where in a building the energy goes.

The Energy page reads a building as one number — its incoming supply against a reference. A
building with a meter per floor can say more: which floors carry the load, which floor's
share has moved, and how much of the incoming supply the floor meters account for between
them. That last figure matters in its own right. Floor meters that sum to 60% of the main
meter say that 40% of the building — plant, lifts, common areas — is unmetered, which is a
different thing from 40% lost.

Each floor is read over the same trailing window as its neighbours, so the shares add up.
kWh come from the meter's own readings; cost is priced at the meter's contracted tariff, and
a sub-meter that carries none (a migration leaves the column default) is priced at the main
meter's rate for its fuel, because the tenant on Level 3 pays what the building pays.
"""
from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .meter_scope import floor_level_from_name

FUEL_ALIAS = {"electric": "electricity", "elec": "electricity"}

_SQL = """
    SELECT m.id::text                      AS meter_id,
           m.building_id::text             AS building_id,
           b.name                          AS building,
           b.building_code                 AS building_code,
           lower(coalesce(m.meter_type, 'electricity')) AS fuel,
           coalesce(m.mpan, m.mprn)        AS supply,
           m.is_sub_meter                  AS is_sub_meter,
           m.tariff_gbp_per_kwh::float     AS tariff,
           m.description                   AS description,
           s.section_id::text              AS section_id,
           s.name                          AS section,
           s.section_type                  AS section_type,
           s.gross_area_m2::float          AS area_m2,
           coalesce(f.name, s.floor_name)  AS floor,
           f.level                         AS level,
           (SELECT sum(r.consumption_kwh) FROM plenum_cafm.meter_readings r
             WHERE r.meter_id = m.id
               AND r.reading_at >= now() - make_interval(days => :days))::float AS kwh,
           (SELECT count(*) FROM plenum_cafm.meter_readings r
             WHERE r.meter_id = m.id
               AND r.reading_at >= now() - make_interval(days => :days))       AS readings,
           (SELECT max(r.reading_at) FROM plenum_cafm.meter_readings r
             WHERE r.meter_id = m.id)                                          AS last_reading_at,
           (SELECT count(*) FROM plenum_cafm.energy_anomalies a
             WHERE a.meter_id = m.id AND a.status = 'open')                     AS open_anomalies
      FROM plenum_cafm.energy_meters m
      JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
      LEFT JOIN plenum_cafm.building_sections s ON s.section_id = m.section_id
      LEFT JOIN plenum_cafm.floors f ON f.floor_id = s.floor_id
     WHERE m.active
       AND m.building_id = ANY(CAST(:bids AS uuid[]))
     ORDER BY b.building_code, f.level NULLS LAST, s.name, m.meter_type
"""


def _fuel(v: str | None) -> str:
    f = (v or "electricity").lower()
    return FUEL_ALIAS.get(f, f)


def group_by_floor(rows: Iterable[dict[str, Any]], *, days: int) -> list[dict[str, Any]]:
    """Pure: the rows of _SQL (or anything shaped like them) as one entry per building.

    Floors are ordered by level, basement first; a sub-meter whose section names no floor is
    listed under "unplaced" rather than dropped, because a meter that was ingested and cannot
    be placed is a fact about the ingest the reader needs.
    """
    by_b: dict[str, dict[str, Any]] = {}
    for r in rows:
        bid = str(r["building_id"])
        b = by_b.get(bid)
        if b is None:
            b = by_b[bid] = {
                "building_id": bid, "building": r.get("building"),
                "building_code": r.get("building_code"), "days": days,
                "mains": [], "floors": {}, "unplaced": [],
            }
        fuel = _fuel(r.get("fuel"))
        kwh = float(r.get("kwh") or 0.0)
        meter = {
            "meter_id": r["meter_id"], "supply": r.get("supply"), "fuel": fuel,
            "kwh": round(kwh, 1), "readings": int(r.get("readings") or 0),
            "last_reading_at": (r["last_reading_at"].isoformat()
                                if r.get("last_reading_at") else None),
            "open_anomalies": int(r.get("open_anomalies") or 0),
            "tariff": float(r["tariff"]) if r.get("tariff") else None,
            "description": r.get("description"),
            "section_id": r.get("section_id"), "section": r.get("section"),
            "section_type": r.get("section_type"),
            "area_m2": float(r["area_m2"]) if r.get("area_m2") else None,
        }
        if not r.get("is_sub_meter"):
            b["mains"].append(meter)
            continue
        floor = r.get("floor")
        if not floor:
            b["unplaced"].append(meter)
            continue
        level = r.get("level")
        if level is None:
            level = floor_level_from_name(floor)
        fl = b["floors"].get(floor)
        if fl is None:
            fl = b["floors"][floor] = {
                "floor": floor, "level": level, "meters": [],
                "area_m2": meter["area_m2"], "section_type": meter["section_type"],
            }
        fl["meters"].append(meter)

    out = []
    for b in by_b.values():
        main_kwh: dict[str, float] = {}
        main_rate: dict[str, float] = {}
        for m in b["mains"]:
            main_kwh[m["fuel"]] = main_kwh.get(m["fuel"], 0.0) + m["kwh"]
            if m["tariff"]:
                main_rate.setdefault(m["fuel"], m["tariff"])
        floors = sorted(b["floors"].values(),
                        key=lambda f: (f["level"] is None, f["level"] if f["level"] is not None else 0, f["floor"]))
        metered: dict[str, float] = {}
        for fl in floors:
            by_fuel: dict[str, float] = {}
            cost = 0.0
            anomalies = 0
            for m in fl["meters"]:
                by_fuel[m["fuel"]] = by_fuel.get(m["fuel"], 0.0) + m["kwh"]
                rate = m["tariff"] or main_rate.get(m["fuel"]) or 0.0
                m["cost"] = round(m["kwh"] * rate, 2)
                m["rate_used"] = rate or None
                # The floor's share of what the building drew on this fuel. None, not zero,
                # when the building has no main meter on the fuel to be a share of.
                m["share_pct"] = (round(m["kwh"] / main_kwh[m["fuel"]] * 100, 1)
                                  if main_kwh.get(m["fuel"]) else None)
                cost += m["cost"]
                anomalies += m["open_anomalies"]
                metered[m["fuel"]] = metered.get(m["fuel"], 0.0) + m["kwh"]
            fl["kwh_by_fuel"] = {k: round(v, 1) for k, v in by_fuel.items()}
            fl["kwh"] = round(sum(by_fuel.values()), 1)
            fl["cost"] = round(cost, 2)
            fl["open_anomalies"] = anomalies
            area = fl.get("area_m2")
            # Annualised intensity of the floor from the window, so floors of different size
            # compare. Trailing window scaled to a year; a short window says so in `days`.
            fl["kwh_per_m2_year"] = (round(fl["kwh"] / area * 365.0 / days, 1)
                                     if area and days else None)
        coverage = {
            fuel: (round(metered.get(fuel, 0.0) / kwh * 100, 1) if kwh else None)
            for fuel, kwh in main_kwh.items()
        }
        for m in b["unplaced"]:
            rate = m["tariff"] or main_rate.get(m["fuel"]) or 0.0
            m["cost"] = round(m["kwh"] * rate, 2)
            m["rate_used"] = rate or None
            m["share_pct"] = (round(m["kwh"] / main_kwh[m["fuel"]] * 100, 1)
                              if main_kwh.get(m["fuel"]) else None)
        sub_count = sum(len(f["meters"]) for f in floors) + len(b["unplaced"])
        out.append({
            "building_id": b["building_id"], "building": b["building"],
            "building_code": b["building_code"], "days": days,
            "mains": b["mains"],
            "main_kwh_by_fuel": {k: round(v, 1) for k, v in main_kwh.items()},
            "floors": floors,
            "unplaced": b["unplaced"],
            "sub_meters": sub_count,
            "floors_metered": len(floors),
            # What the floor meters account for of the incoming supply, per fuel. The rest is
            # the building's own load — plant, lifts, common parts — or unmetered.
            "coverage_pct_by_fuel": coverage,
            "summary": _summary(len(floors), sub_count, coverage, days),
        })
    return out


def _summary(floors: int, subs: int, coverage: dict[str, float | None], days: int) -> str:
    if not subs:
        return "No sub-meters on record — the building is read at its incoming supply only."
    parts = [f"{subs} sub-meter{'s' if subs != 1 else ''} on {floors} floor{'s' if floors != 1 else ''}"]
    for fuel in ("electricity", "gas"):
        c = coverage.get(fuel)
        if c is not None:
            parts.append(f"floors account for {c:g}% of the {fuel} supply")
    return " · ".join(parts) + f" · last {days} days"


async def by_floor(
    session: AsyncSession, *, building_ids: list[UUID] | None, days: int = 30,
) -> dict[str, Any]:
    if not building_ids:
        return {"ok": True, "days": days, "buildings": []}
    rows = (await session.execute(
        text(_SQL), {"bids": [str(b) for b in building_ids], "days": int(days)},
    )).mappings().all()
    buildings = group_by_floor([dict(r) for r in rows], days=days)
    return {"ok": True, "days": days, "count": len(buildings), "buildings": buildings}
