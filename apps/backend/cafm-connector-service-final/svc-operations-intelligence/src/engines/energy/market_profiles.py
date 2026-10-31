"""The market profile table — benchmark, data source and commercial terms per market.

The Energy page lays four markets side by side so a reader can see why the headline numbers
are normalised rather than compared: the standards, units, routes and currencies differ.
That table was a constant in the frontend. It is now assembled here, and the difference
matters because most of its cells are one of two different kinds of fact:

**Reference** — things no database can derive. The name of the standard, the identifier a
supply point carries, what the utility will and will not hand over, a published tariff.
These live in ``src/reference/energy_market_profiles.json`` with a source and, for a tariff,
an as-of date, and every such cell is served labelled ``reference``.

**Measured** — things this deployment knows about itself. How many buildings sit in each
market. The tariff actually contracted on its meters, which overrides the published one.
The routes actually on record. And the reference EUI where the platform computes one: the
UK figure is TM46 weighted by the use mix of the buildings actually in scope, and the UAE
figure is the rolling median of comparable buildings in this portfolio, because no
operational standard is published there. Those cells are labelled ``measured`` or
``derived``, and the cell says how many buildings or meters stand behind it.

A cell never silently mixes the two: a reader sees "28.4p/kWh · reference" until the day a
meter with a contracted tariff is on record, and then sees that instead.
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import detectors
from .buildings import _ROLLING_MIN_COMPARABLES, tm46_type_for
from .eui import load_tm46
from . import us_ratings
from .ratings_position import BCA_OFFICE_REFERENCE_KWH_M2

log = get_logger(__name__)

_PATH = Path(__file__).resolve().parents[2] / "reference" / "energy_market_profiles.json"
_CACHE: dict[str, Any] | None = None

#: The markets the page lays side by side, in the order it lays them.
MARKET_ORDER = ("UK", "US", "AE", "SG")

#: How a currency amount reads per kWh in each market — the small unit where one is in
#: everyday use, because "0.284 GBP/kWh" is not how anyone quotes a UK tariff.
_PER_KWH = {
    "GBP": lambda v: f"{v * 100:.1f}p/kWh",
    "USD": lambda v: f"${v:.2f}/kWh",
    "AED": lambda v: f"{v * 100:.1f} fils/kWh",
    "SGD": lambda v: f"S${v:.2f}/kWh",
}


def load_profiles() -> dict[str, Any]:
    global _CACHE
    if _CACHE is None:
        _CACHE = json.loads(_PATH.read_text(encoding="utf-8"))
    return _CACHE


def _cell(value: Any, basis: str, *, n: int | None = None, note: str | None = None) -> dict[str, Any]:
    """One cell of the table: the text, where it came from, and how much stands behind it."""
    out: dict[str, Any] = {"v": value if value not in (None, "") else "—", "basis": basis}
    if n is not None:
        out["n"] = n
    if note:
        out["note"] = note
    return out


def _published(pub: Any) -> str:
    """The text of a published tariff, whatever shape the reference file holds it in.

    These entries used to be plain strings and are now objects carrying the numbers the
    pricing engine needs — low, high, unit — alongside the words. A table cell wants the
    words. Both shapes are accepted because a market that has not been converted yet must
    still render, and a dict must never reach the page as a cell value.
    """
    if isinstance(pub, dict):
        if pub.get("unpriceable"):
            # LPG in the UAE is sold by weight, so there is no per-kWh rate to show. Saying
            # so is the answer; a blank cell reads as "we did not look".
            return " · ".join(x for x in (pub.get("label"), pub.get("basis")) if x) or "not priced per kWh"
        return " · ".join(x for x in (pub.get("label"), pub.get("basis")) if x) or "—"
    return str(pub) if pub not in (None, "") else "—"


def per_kwh(value: float, currency: str) -> str:
    fmt = _PER_KWH.get(str(currency or "").upper())
    return fmt(value) if fmt else f"{value:.3f} {currency}/kWh"


def weighted_tm46(buildings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """TM46 electricity+gas benchmark weighted by the use mix of the buildings given.

    Weighted by floor area where it is known and by count where it is not, because a
    38,000 m² office and a 4,000 m² one should not pull the portfolio figure equally. Returns
    the figure, the per-type figures that made it up, and how many buildings had a mappable
    type — a portfolio of 40 buildings with 3 typed ones is a 3-building figure and says so.
    """
    pack = load_tm46()
    types = pack.get("building_types") or {}
    weighted_sum = weight_sum = 0.0
    by_type: dict[str, dict[str, float]] = {}
    counted = 0
    for b in buildings:
        key = tm46_type_for(b.get("building_type") or b.get("use_type") or b.get("primary_use"))
        bt = types.get(key or "")
        if not bt:
            continue
        bench = float(bt.get("electricity") or 0) + float(bt.get("gas") or 0)
        if bench <= 0:
            continue
        w = float(b.get("gfa_sqm") or 0) or 1.0
        weighted_sum += bench * w
        weight_sum += w
        counted += 1
        slot = by_type.setdefault(key, {"benchmark": bench, "buildings": 0})
        slot["buildings"] += 1
    if not counted:
        return None
    return {"value": round(weighted_sum / weight_sum, 0), "by_type": by_type, "buildings": counted}


def rolling_portfolio_median(buildings: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The rolling benchmark for a market with no operational standard: the median EUI of
    the buildings in it that have a reading. Needs the same minimum the row-level benchmark
    needs, or it is not a benchmark, it is one building's number wearing the word."""
    euis = [float(b["eui_kwh_per_m2"]) for b in buildings
            if isinstance(b.get("eui_kwh_per_m2"), (int, float))]
    if len(euis) < _ROLLING_MIN_COMPARABLES:
        return None
    return {"value": round(median(euis), 0), "buildings": len(euis)}


def ll97_reference(buildings: list[dict[str, Any]], *, year: int) -> dict[str, Any] | None:
    """The US benchmark rule applied to the buildings in scope: NYC LL97's emissions cap for
    the current period, by the occupancy group each building's use maps to, area-weighted —
    alongside the Energy Star certification score, which is one number for everyone.

    Reported in the unit the law is written in, tCO₂e per ft² per year. A building whose use
    maps to no group takes group B, as ll97_position itself does; a portfolio with no US
    building gets the rule stated, not a number invented for it.
    """
    period = us_ratings.ll97_period_for(year)
    if not period or not buildings:
        return None
    limits = us_ratings.LL97_LIMITS[period]
    weighted = weight = 0.0
    groups: dict[str, int] = {}
    for b in buildings:
        g = us_ratings.occupancy_group_for(b.get("building_type"))
        cap = limits.get(g) or limits["B"]
        w = float(b.get("gfa_sqm") or 0) or 1.0
        weighted += cap * w
        weight += w
        groups[g] = groups.get(g, 0) + 1
    return {"cap_tco2e_per_ft2": weighted / weight, "period": period, "groups": groups,
            "buildings": len(buildings), "energy_star_target": us_ratings.CERTIFICATION_SCORE}


def bca_reference(buildings: list[dict[str, Any]]) -> dict[str, Any]:
    """The Singapore rule: a building reads against its own recorded BCA benchmark where
    one is on the site, and against the BCA office median otherwise — the same precedence
    ratings_position applies per building, taken area-weighted across the ones in scope."""
    if not buildings:
        return {"value": BCA_OFFICE_REFERENCE_KWH_M2, "buildings": 0, "recorded": 0}
    weighted = weight = 0.0
    recorded = 0
    for b in buildings:
        own = b.get("benchmark_kwh_per_m2")
        ref = float(own) if isinstance(own, (int, float)) else BCA_OFFICE_REFERENCE_KWH_M2
        recorded += 1 if isinstance(own, (int, float)) else 0
        w = float(b.get("gfa_sqm") or 0) or 1.0
        weighted += ref * w
        weight += w
    return {"value": round(weighted / weight, 0), "buildings": len(buildings), "recorded": recorded}


async def _buildings_by_market(session: AsyncSession, building_ids: list[UUID]) -> dict[str, list[dict[str, Any]]]:
    """The buildings in scope, grouped by the market each sits in — country off the site,
    which is where it lives on this deployment, and the EUI/area off the same row."""
    if not building_ids:
        return {}
    rows = (await session.execute(text("""
        SELECT b.building_id::text AS building_id, b.name,
               upper(coalesce(l.country_code, s.country_code,
                              b.raw_metadata->>'country_code')) AS country_code,
               s.use_type, s.site_type, b.primary_use::text AS primary_use,
               s.eui_kwh_per_m2::float AS eui_kwh_per_m2,
               s.benchmark_kwh_per_m2::float AS benchmark_kwh_per_m2,
               coalesce(s.gfa_sqm::float, b.gross_area_sqft::float / 10.7639) AS gfa_sqm
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id::text = b.site_id::text OR s.site_id::text = b.site_id::text
          -- Same two-clause match building_rollup.py uses: locations.id is uuid on this
          -- database and integer on the other, so the second clause rebuilds the uuid form.
          LEFT JOIN plenum_cafm.locations l ON (
                l.id::text = b.location_id::text
             OR '00000000-0000-0000-0000-' || lpad(l.id::text, 12, '0') = b.location_id::text
          )
         WHERE b.building_id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": [str(b) for b in building_ids]})).mappings().all()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        d["building_type"] = d.get("use_type") or d.get("site_type") or d.get("primary_use")
        out.setdefault(d["country_code"] or "—", []).append(d)
    return out


async def _meters_by_market(session: AsyncSession, building_ids: list[UUID]) -> dict[str, dict[str, Any]]:
    """What the meters actually on record say per market: how many, the mean contracted
    tariff, and the routes they arrive by."""
    if not building_ids:
        return {}
    try:
        async with session.begin_nested():
            rows = (await session.execute(text("""
                SELECT upper(s.country_code) AS country_code,
                       count(*) AS meters,
                       avg(m.tariff_gbp_per_kwh)::float AS tariff,
                       count(m.tariff_gbp_per_kwh) AS priced,
                       array_remove(array_agg(DISTINCT coalesce(m.raw_metadata->>'route', s.metering_route)), NULL)
                           AS routes,
                       array_remove(array_agg(DISTINCT s.metering_granularity), NULL) AS granularities
                  FROM plenum_cafm.energy_meters m
                  JOIN plenum_cafm.buildings b ON b.building_id = m.building_id
                  LEFT JOIN plenum_cafm.sites s ON s.id::text = b.site_id::text OR s.site_id::text = b.site_id::text
                 WHERE m.active AND m.building_id = ANY(CAST(:ids AS uuid[]))
                 GROUP BY 1
            """), {"ids": [str(b) for b in building_ids]})).mappings().all()
    except Exception as exc:  # noqa: BLE001 — the table stands without the meter column
        log.warning("energy.market_profiles.meters_failed", error=str(exc)[:200])
        return {}
    return {r["country_code"]: dict(r) for r in rows if r["country_code"]}


async def market_profiles(
    session: AsyncSession,
    *,
    building_ids: list[UUID],
    markets: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """The table, one column per market, every cell saying what kind of fact it is."""
    ref = load_profiles()
    by_market = await _buildings_by_market(session, building_ids)
    meters = await _meters_by_market(session, building_ids)
    order = tuple(m for m in (markets or MARKET_ORDER) if m in ref["markets"])

    columns: dict[str, Any] = {}
    for cc in order:
        r = ref["markets"][cc]
        blds = by_market.get(cc, [])
        mt = meters.get(cc) or {}
        currency = detectors.currency_for(cc)

        # Reference EUI — derived where the platform computes one, reference otherwise.
        ref_eui = r.get("reference_eui") or {}
        if cc == "UK":
            # The statutory figure, flat, because that is the number the UK is actually held
            # to and the one the country rule tests against. The use-mix weighting the
            # platform can compute is a more precise read of the same standard, so it is
            # carried in the note rather than dropped — but the headline is the statutory one.
            statutory = ref_eui.get("statutory_value")
            w = weighted_tm46(blds)
            weighted_note = (
                f"; weighted by the use mix of the {w['buildings']} buildings in scope this "
                f"is {int(w['value'])} ("
                + ", ".join(f"{k} {int(v['benchmark'])}" for k, v in sorted(w["by_type"].items()))
                + ")" if w else "")
            ref_cell = (_cell(f"{int(statutory)} kWh/m²/yr statutory", "reference",
                              note=(ref_eui.get("statutory_basis") or "CIBSE TM46 statutory")
                                   + weighted_note)
                        if statutory is not None else
                        _cell("TM46 by use class · no statutory figure on file", "reference"))
        elif cc == "AE":
            m = rolling_portfolio_median(blds)
            ref_cell = (_cell(f"{int(m['value'])} kWh/m²/yr rolling portfolio benchmark", "derived",
                              n=m["buildings"], note="median EUI of the buildings in this market with a reading")
                        if m else _cell(f"rolling portfolio benchmark · needs {_ROLLING_MIN_COMPARABLES} buildings "
                                        f"with an EUI, {sum(1 for b in blds if b.get('eui_kwh_per_m2') is not None)} have one",
                                        "derived", n=len(blds)))
        elif cc == "US":
            from datetime import date
            u = ll97_reference(blds, year=date.today().year)
            ref_cell = (_cell(f"LL97 cap {u['cap_tco2e_per_ft2']:.5f} tCO₂e/ft²/yr ({u['period']}) · "
                              f"Energy Star {u['energy_star_target']} for certification",
                              "derived", n=u["buildings"],
                              note="LL97 limit by occupancy group, area-weighted over the buildings in scope: "
                                   + ", ".join(f"{g}×{n}" for g, n in sorted(u["groups"].items())))
                        if u else _cell(f"Energy Star {us_ratings.CERTIFICATION_SCORE} for certification · "
                                        "LL97 limit by occupancy group", "reference",
                                        note="no US building in scope to apply the rule to"))
        elif cc == "SG":
            g = bca_reference(blds)
            ref_cell = (_cell(f"{int(g['value'])} kWh/m²/yr BCA reference", "derived", n=g["buildings"],
                              note=(f"{g['recorded']} of {g['buildings']} buildings carry their own BCA "
                                    f"benchmark; the rest read against the office median "
                                    f"{int(BCA_OFFICE_REFERENCE_KWH_M2)}"))
                        if g["buildings"] else
                        _cell(f"{int(BCA_OFFICE_REFERENCE_KWH_M2)} kWh/m²/yr office reference", "reference",
                              note="BCA Building Energy Benchmarking Report · no SG building in scope"))
        else:
            ref_cell = _cell(ref_eui.get("label"), "reference", note=ref_eui.get("source"))

        # Electricity — the contracted tariff on this market's meters beats a published one.
        pub = r.get("elec") or {}
        if mt.get("priced"):
            elec_cell = _cell(f"{per_kwh(float(mt['tariff']), currency)} contracted",
                              "measured", n=int(mt["priced"]),
                              note=f"mean of the tariffs on {int(mt['priced'])} active meters")
        else:
            elec_cell = _cell(_published(pub), "reference",
                              note=f"published figure as of {pub.get('as_of')}" if pub.get("as_of") else None)

        # Gas is the same shape as electricity and has to be read the same way. It was left
        # as _cell(r.get("gas")) when the published tariffs became objects, so the whole
        # {label, basis, as_of, source, low, high, unit} dict went out as the cell's text and
        # the page tried to render a dict as a table cell. A cell is a string; the numbers on
        # that object are for GET /api/energy/tariffs, which is what they are for.
        gas_cell = _cell(_published(r.get("gas")), "reference",
                         note=f"published figure as of {(r.get('gas') or {}).get('as_of')}"
                              if isinstance(r.get("gas"), dict) and (r.get("gas") or {}).get("as_of") else None)

        # Route — what the meters on record actually arrive by, else the market's usual.
        routes = [x for x in (mt.get("routes") or []) if x]
        route_cell = (_cell(" · ".join(sorted(routes)), "measured", n=int(mt.get("meters") or 0),
                            note="routes on the active meters in scope")
                      if routes else _cell(r.get("route"), "reference"))

        columns[cc] = {
            "name": r["name"],
            "flag": r["flag"],
            "buildings": len(blds),
            "meters": int(mt.get("meters") or 0),
            "cells": {
                "std": _cell(r.get("std"), "reference"),
                "ref": ref_cell,
                "unit": _cell(r.get("unit"), "reference"),
                "route": route_cell,
                "refresh": _cell(r.get("refresh"), "reference"),
                "per": _cell(r.get("per"), "reference"),
                "ident": _cell(r.get("ident"), "reference"),
                "limits": _cell(r.get("limits"), "reference"),
                "elec": elec_cell,
                "gas": gas_cell,
                "other": _cell(r.get("other"), "reference"),
                # Derived from the market, not typed into it: the same rule every anomaly
                # figure and every report line now follows.
                "cur": _cell(currency, "derived"),
            },
        }

    unplaced = len(by_market.get("—", []))
    return {
        "ok": True,
        "attributes": ref["attributes"],
        "order": list(order),
        "markets": columns,
        "buildings_in_scope": len(building_ids),
        "buildings_without_a_market": unplaced,
        "basis_note": ("reference = a published fact about the market · measured = read off this "
                       "deployment's own records · derived = computed from them"),
    }
