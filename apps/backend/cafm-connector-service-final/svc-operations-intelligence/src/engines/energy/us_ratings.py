"""B6 and B7 — the two US positions the energy page shows and nothing computed.

ENERGY STAR score (B6). Portfolio Manager scores a building 1–100 by comparing its source
energy use intensity with the distribution for its property type, adjusted for operating
characteristics, using regression models EPA publishes in its Technical Reference. The
regression tables are property-type specific and not shipped with this platform, so what is
computed here is an ESTIMATE on the same architecture: site kWh by fuel → source energy
(EPA's national site-to-source factors) → source EUI in kBtu/ft² → ratio to the property
type's national median (CBECS-based medians from the Technical Reference) → percentile from
a lognormal fit whose dispersion EPA's published score tables imply. A building at the
median scores 50; one using about a quarter less scores 75 (the certification threshold). Every
result says "estimate" and names the median it was read against, because an estimate that
looks like the official score is worse than none.

LL97 (B7). New York City's Local Law 97 caps annual building emissions per square foot by
occupancy group, in compliance periods (2024–2029, then 2030–2034), with emissions computed
from utility kWh, therms and steam using the coefficients in the rule, and a penalty of
$268 per tCO₂e over the cap. Those figures are law, not estimates, and are reproduced here
from the rule text (1 RCNY §103-14). The building's occupancy group is mapped from its
primary use; a mixed-use building's cap is the area-weighted blend of its uses.

Both take twelve months of readings. With fewer, the position is projected and says so:
the frontend's rule is actual at 12 months, projected from 3, not shown below.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import EnergyMeter, EnergyRating, MeterReading

log = get_logger(__name__)

KWH_TO_KBTU = 3.412
M2_TO_FT2 = 10.7639
THERM_KBTU = 100.0
MIN_MONTHS_TO_SHOW = 3
ACTUAL_MONTHS = 12

# ── ENERGY STAR ────────────────────────────────────────────────────────────────────
#: EPA national site-to-source conversion factors (Portfolio Manager Technical Reference,
#: "Source Energy", 2023 revision).
SOURCE_FACTORS = {"electricity": 2.80, "natural_gas": 1.05, "gas": 1.05, "district_steam": 1.20,
                  "fuel_oil": 1.01, "propane": 1.01}
#: National median SOURCE EUI (kBtu/ft²/yr) by property type — Portfolio Manager Technical
#: Reference "U.S. Energy Use Intensity by Property Type" (CBECS 2018 basis, 2024 table).
MEDIAN_SOURCE_EUI: dict[str, float] = {
    "office": 116.4, "medical_office": 121.7, "retail": 129.5, "supermarket": 444.0,
    "multifamily": 118.1, "hotel": 146.7, "hospital": 426.9, "k12_school": 104.4,
    "college": 180.6, "warehouse": 45.2, "distribution_center": 45.2, "laboratory": 318.2,
    "data_center": 1_000.0, "worship": 62.0, "bank": 209.3, "restaurant": 573.7,
    "fitness": 175.1, "mixed_use": 118.6, "other": 118.6,
}
#: Dispersion of the lognormal fit per property type. 0.43 reproduces EPA's office table
#: (score 75 ≈ 25% below median; score 25 ≈ 33% above). Wider for types with more variance.
LOGNORMAL_SIGMA: dict[str, float] = {"office": 0.43, "retail": 0.48, "multifamily": 0.36,
                                     "hotel": 0.40, "hospital": 0.30, "k12_school": 0.42,
                                     "warehouse": 0.60, "laboratory": 0.45, "mixed_use": 0.45}
DEFAULT_SIGMA = 0.45
CERTIFICATION_SCORE = 75

#: primary_use (plenum_cafm.building_primary_use) / use_type → ENERGY STAR property type
USE_TO_PROPERTY_TYPE: dict[str, str] = {
    "commercial": "office", "office": "office", "retail": "retail", "mall": "retail",
    "residential": "multifamily", "multifamily": "multifamily", "hotel": "hotel",
    "hospital": "hospital", "healthcare": "hospital", "education": "k12_school",
    "school": "k12_school", "university": "college", "industrial": "warehouse",
    "logistics": "distribution_center", "warehouse": "warehouse", "laboratory": "laboratory",
    "leisure": "fitness", "mixed": "mixed_use", "other": "other",
}


def _phi(z: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def property_type_for(use: str | None) -> str:
    return USE_TO_PROPERTY_TYPE.get((use or "").strip().lower(), "other")


def energy_star_estimate(
    *,
    kwh_by_fuel: dict[str, float],
    gfa_m2: float,
    property_type: str,
    months: int = 12,
) -> dict[str, Any]:
    """Estimated ENERGY STAR score from kWh by fuel over ``months`` and the floor area.

    Annualised to twelve months before scoring, so a nine-month window is scored on what a
    full year at that rate would use. ``months`` < 12 marks the result projected.
    """
    if gfa_m2 <= 0:
        return {"ok": False, "error": "gfa_m2_must_be_positive"}
    if months <= 0:
        return {"ok": False, "error": "no_months"}
    ptype = property_type if property_type in MEDIAN_SOURCE_EUI else "other"
    scale = 12.0 / months
    source_kbtu = 0.0
    site_kbtu = 0.0
    by_fuel = {}
    for fuel, kwh in kwh_by_fuel.items():
        factor = SOURCE_FACTORS.get(fuel, 1.0)
        kbtu = float(kwh) * KWH_TO_KBTU * scale
        site_kbtu += kbtu
        source_kbtu += kbtu * factor
        by_fuel[fuel] = {"kwh_annualised": round(float(kwh) * scale, 2), "source_factor": factor}
    ft2 = gfa_m2 * M2_TO_FT2
    source_eui = source_kbtu / ft2
    site_eui = site_kbtu / ft2
    median_eui = MEDIAN_SOURCE_EUI[ptype]
    sigma = LOGNORMAL_SIGMA.get(ptype, DEFAULT_SIGMA)
    if source_eui <= 0:
        return {"ok": False, "error": "no_consumption"}
    # score = percentile of buildings that use MORE energy than this one
    z = math.log(source_eui / median_eui) / sigma
    score = int(round(100.0 * (1.0 - _phi(z))))
    score = max(1, min(100, score))
    # the source EUI that would score 75, and the reduction needed to get there
    target_eui = median_eui * math.exp(sigma * -0.6745)  # z at the 25th percentile
    reduction_pct = max(0.0, 100.0 * (1.0 - target_eui / source_eui))
    return {
        "ok": True,
        "scheme": "energy_star",
        "score": score,
        "estimate": True,
        "certification_score": CERTIFICATION_SCORE,
        "points_short": max(0, CERTIFICATION_SCORE - score),
        "status": "certifiable" if score >= CERTIFICATION_SCORE else "short",
        "property_type": ptype,
        "gfa_ft2": round(ft2, 1),
        "site_eui_kbtu_ft2": round(site_eui, 2),
        "source_eui_kbtu_ft2": round(source_eui, 2),
        "median_source_eui_kbtu_ft2": median_eui,
        "reduction_to_certify_pct": round(reduction_pct, 1),
        "months_of_data": months,
        "basis": "actual" if months >= ACTUAL_MONTHS else "projected",
        "by_fuel": by_fuel,
        "method": ("Estimate: source EUI vs national median (Portfolio Manager Technical "
                   "Reference), lognormal percentile. Not an official Portfolio Manager score."),
    }


# ── LL97 ───────────────────────────────────────────────────────────────────────────
#: tCO₂e per unit, by compliance period (1 RCNY §103-14 Table 1 / LL97 §28-320.3.1).
#: Electricity per kWh; natural gas per kBtu; district steam per kBtu; fuel oil per kBtu.
LL97_COEFFICIENTS: dict[str, dict[str, float]] = {
    "2024-2029": {"electricity": 0.000288962, "natural_gas": 0.00005311,
                  "district_steam": 0.00004493, "fuel_oil_2": 0.00007421, "fuel_oil_4": 0.00007529},
    "2030-2034": {"electricity": 0.000145, "natural_gas": 0.00005311,
                  "district_steam": 0.00004493, "fuel_oil_2": 0.00007421, "fuel_oil_4": 0.00007529},
}
#: Emissions limits, tCO₂e per ft² per year, by occupancy group and period (LL97 §28-320.3.1
#: and §28-320.3.2). Group letters are the NYC Building Code's.
LL97_LIMITS: dict[str, dict[str, float]] = {
    "2024-2029": {"A": 0.01074, "B": 0.00846, "B_healthcare": 0.02381, "E": 0.00758,
                  "F": 0.00574, "H": 0.02381, "I-1": 0.01138, "I-2": 0.02381, "I-3": 0.02381,
                  "I-4": 0.00758, "M": 0.01181, "R-1": 0.00987, "R-2": 0.00675, "S": 0.00426,
                  "U": 0.00426},
    "2030-2034": {"A": 0.00420, "B": 0.00453, "B_healthcare": 0.01193, "E": 0.00344,
                  "F": 0.00167, "H": 0.01193, "I-1": 0.00598, "I-2": 0.01193, "I-3": 0.01193,
                  "I-4": 0.00344, "M": 0.00403, "R-1": 0.00526, "R-2": 0.00407, "S": 0.00110,
                  "U": 0.00110},
}
LL97_PENALTY_USD_PER_TCO2E = 268.0
#: primary use → occupancy group
USE_TO_OCCUPANCY_GROUP: dict[str, str] = {
    "commercial": "B", "office": "B", "retail": "M", "mall": "M", "residential": "R-2",
    "multifamily": "R-2", "hotel": "R-1", "hospital": "I-2", "healthcare": "B_healthcare",
    "education": "E", "school": "E", "university": "B", "industrial": "F", "logistics": "S",
    "warehouse": "S", "laboratory": "B", "leisure": "A", "mixed": "B", "other": "B",
}


def ll97_period_for(year: int) -> str | None:
    if 2024 <= year <= 2029:
        return "2024-2029"
    if 2030 <= year <= 2034:
        return "2030-2034"
    return None


def occupancy_group_for(use: str | None) -> str:
    return USE_TO_OCCUPANCY_GROUP.get((use or "").strip().lower(), "B")


def ll97_position(
    *,
    kwh_by_fuel: dict[str, float],
    gfa_m2: float,
    year: int,
    primary_use: str | None = None,
    use_mix: list[dict[str, Any]] | None = None,
    months: int = 12,
) -> dict[str, Any]:
    """Annual emissions against the LL97 cap for one building in one compliance year.

    ``kwh_by_fuel`` is site kWh over ``months`` (gas and steam already in kWh, as the
    readings tables hold them; they are converted to kBtu here). ``use_mix`` is the
    building's [{use, pct}] list; when present the cap is the area-weighted blend of each
    use's limit, as the law provides for mixed-use buildings.
    """
    period = ll97_period_for(year)
    if not period:
        return {"ok": False, "error": "year_outside_ll97_periods", "year": year}
    if gfa_m2 <= 0:
        return {"ok": False, "error": "gfa_m2_must_be_positive"}
    if months <= 0:
        return {"ok": False, "error": "no_months"}
    scale = 12.0 / months
    coef = LL97_COEFFICIENTS[period]
    limits = LL97_LIMITS[period]
    ft2 = gfa_m2 * M2_TO_FT2

    emissions = 0.0
    by_fuel: dict[str, Any] = {}
    for fuel, kwh in kwh_by_fuel.items():
        annual_kwh = float(kwh) * scale
        key = {"gas": "natural_gas", "steam": "district_steam", "fuel_oil": "fuel_oil_2"}.get(fuel, fuel)
        if key == "electricity":
            t = annual_kwh * coef["electricity"]
        elif key in coef:
            t = annual_kwh * KWH_TO_KBTU * coef[key]
        else:
            t = 0.0
        emissions += t
        by_fuel[fuel] = {"kwh_annualised": round(annual_kwh, 2), "tco2e": round(t, 4)}

    groups: list[tuple[str, float]] = []
    if use_mix:
        total_pct = sum(float(u.get("pct") or 0) for u in use_mix) or 100.0
        for u in use_mix:
            groups.append((occupancy_group_for(u.get("use")), float(u.get("pct") or 0) / total_pct))
    else:
        groups.append((occupancy_group_for(primary_use), 1.0))
    limit_per_ft2 = sum(limits.get(g, limits["B"]) * w for g, w in groups)
    cap = limit_per_ft2 * ft2
    over = max(0.0, emissions - cap)
    headroom_pct = 100.0 * (cap - emissions) / cap if cap > 0 else None
    return {
        "ok": True,
        "scheme": "ll97",
        "year": year,
        "compliance_period": period,
        "occupancy_groups": [{"group": g, "weight": round(w, 3)} for g, w in groups],
        "gfa_ft2": round(ft2, 1),
        "emissions_tco2e": round(emissions, 3),
        "cap_tco2e": round(cap, 3),
        "limit_tco2e_per_ft2": round(limit_per_ft2, 6),
        "intensity_tco2e_per_ft2": round(emissions / ft2, 6),
        "headroom_pct": round(headroom_pct, 1) if headroom_pct is not None else None,
        "over_tco2e": round(over, 3),
        "penalty_usd": round(over * LL97_PENALTY_USD_PER_TCO2E, 2),
        "status": "within" if over <= 0 else "over",
        "months_of_data": months,
        "basis": "actual" if months >= ACTUAL_MONTHS else "projected",
        "by_fuel": by_fuel,
        "coefficients": coef,
        "source": "LL97 §28-320.3 · 1 RCNY §103-14",
    }


# ── the readings behind both ───────────────────────────────────────────────────────

@dataclass
class ConsumptionWindow:
    kwh_by_fuel: dict[str, float]
    months: int
    period_start: date
    period_end: date
    meters: int


async def consumption_for_building(
    session: AsyncSession, *, building_id: UUID, months: int = 12, end: date | None = None
) -> ConsumptionWindow | None:
    """Site kWh by fuel over the trailing window, from every active meter on the building.

    Meters are found two ways because there are two registries: energy_meters keyed on the
    building (site_id holds the building id for buildings adopted from sites), and
    plenum_cafm.meters keyed on building_id whose mpan_mprn matches an energy meter.
    The months counted are months that actually have readings, so a window with a two-month
    hole is scored on ten, not twelve.
    """
    end = end or date.today()
    start = (end.replace(day=1) - timedelta(days=1)).replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    # start and end are bound as dates, not ISO strings: the driver binds a CAST(... AS date)
    # parameter as a date and rejects a str with "no attribute 'toordinal'". And nothing that
    # looks like a bind parameter may appear in the SQL below — including inside a comment.
    rows = (await session.execute(text("""
        WITH ms AS (
            SELECT em.id, em.meter_type
              FROM plenum_cafm.energy_meters em
             WHERE em.active AND em.site_id = CAST(:b AS uuid)
            UNION
            SELECT em.id, em.meter_type
              FROM plenum_cafm.meters m
              JOIN plenum_cafm.energy_meters em
                ON em.active AND (em.mpan = m.mpan_mprn OR em.mprn = m.mpan_mprn)
             WHERE m.building_id = CAST(:b AS uuid)
        )
        SELECT ms.meter_type, date_trunc('month', r.reading_at)::date AS month,
               sum(r.consumption_kwh) AS kwh, count(DISTINCT ms.id) AS meters
          FROM ms JOIN plenum_cafm.meter_readings r ON r.meter_id = ms.id
         WHERE r.reading_at >= CAST(:s AS date) AND r.reading_at < CAST(:e AS date) + 1
         GROUP BY 1, 2
    """), {"b": str(building_id), "s": start, "e": end})).mappings().all()
    if not rows:
        return None
    by_fuel: dict[str, float] = {}
    months_seen: set[date] = set()
    meters = 0
    for r in rows:
        fuel = {"electric": "electricity", "elec": "electricity"}.get(
            (r["meter_type"] or "electricity").lower(), (r["meter_type"] or "electricity").lower())
        by_fuel[fuel] = by_fuel.get(fuel, 0.0) + float(r["kwh"] or 0)
        months_seen.add(r["month"])
        meters = max(meters, int(r["meters"] or 0))
    return ConsumptionWindow(kwh_by_fuel=by_fuel, months=len(months_seen),
                             period_start=min(months_seen), period_end=end, meters=meters)


async def _building_facts(session: AsyncSession, building_id: UUID) -> dict[str, Any] | None:
    row = (await session.execute(text("""
        SELECT b.building_id::text, b.name, b.primary_use::text AS primary_use, b.gross_area_sqft,
               b.organization_id::text AS organization_id,
               s.use_type, s.use_mix, s.gfa_sqm, s.country_code
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
         WHERE b.building_id = CAST(:b AS uuid)
         LIMIT 1
    """), {"b": str(building_id)})).mappings().first()
    if not row:
        return None
    gfa_m2 = None
    if row["gross_area_sqft"]:
        gfa_m2 = float(row["gross_area_sqft"]) / M2_TO_FT2
    elif row["gfa_sqm"]:
        try:
            gfa_m2 = float(row["gfa_sqm"])
        except (TypeError, ValueError):
            gfa_m2 = None
    return {**dict(row), "gfa_m2": gfa_m2, "use": row["use_type"] or row["primary_use"]}


async def _snapshot(
    session: AsyncSession, *, building_id: UUID, organization_id: str | None, scheme: str,
    window: ConsumptionWindow, value: float | None, unit: str, limit: float | None,
    status: str, detail: dict[str, Any],
) -> str:
    row = EnergyRating(
        id=uuid4(), organization_id=UUID(organization_id) if organization_id else None,
        building_id=building_id, scheme=scheme, period_start=window.period_start,
        period_end=window.period_end, months_of_data=window.months,
        value=Decimal(str(round(value, 6))) if value is not None else None, unit=unit,
        limit_value=Decimal(str(round(limit, 6))) if limit is not None else None,
        status=status, basis="consumption", detail_json=detail,
    )
    session.add(row)
    await session.flush()
    return str(row.id)


async def compute_energy_star(
    session: AsyncSession, *, building_id: UUID, months: int = 12, persist: bool = True
) -> dict[str, Any]:
    facts = await _building_facts(session, building_id)
    if not facts:
        return {"ok": False, "error": "building_not_found"}
    if not facts["gfa_m2"]:
        return {"ok": False, "error": "gross_area_required", "building_id": str(building_id)}
    window = await consumption_for_building(session, building_id=building_id, months=months)
    if window is None or window.months < MIN_MONTHS_TO_SHOW:
        return {"ok": False, "error": "insufficient_data", "months_of_data": window.months if window else 0,
                "minimum_months": MIN_MONTHS_TO_SHOW, "building_id": str(building_id)}
    out = energy_star_estimate(kwh_by_fuel=window.kwh_by_fuel, gfa_m2=facts["gfa_m2"],
                               property_type=property_type_for(facts["use"]), months=window.months)
    if not out.get("ok"):
        return out
    out.update({"building_id": str(building_id), "building_name": facts["name"],
                "period_start": window.period_start.isoformat(), "period_end": window.period_end.isoformat(),
                "meters": window.meters})
    if persist:
        out["rating_id"] = await _snapshot(
            session, building_id=building_id, organization_id=facts["organization_id"],
            scheme="energy_star", window=window, value=float(out["score"]), unit="score",
            limit=float(CERTIFICATION_SCORE), status=out["status"], detail=out)
        await session.commit()
    return out


async def compute_ll97(
    session: AsyncSession, *, building_id: UUID, year: int | None = None, months: int = 12,
    persist: bool = True,
) -> dict[str, Any]:
    facts = await _building_facts(session, building_id)
    if not facts:
        return {"ok": False, "error": "building_not_found"}
    if not facts["gfa_m2"]:
        return {"ok": False, "error": "gross_area_required", "building_id": str(building_id)}
    window = await consumption_for_building(session, building_id=building_id, months=months)
    if window is None or window.months < MIN_MONTHS_TO_SHOW:
        return {"ok": False, "error": "insufficient_data", "months_of_data": window.months if window else 0,
                "minimum_months": MIN_MONTHS_TO_SHOW, "building_id": str(building_id)}
    use_mix = facts.get("use_mix") if isinstance(facts.get("use_mix"), list) else None
    out = ll97_position(kwh_by_fuel=window.kwh_by_fuel, gfa_m2=facts["gfa_m2"],
                        year=year or window.period_end.year, primary_use=facts["use"],
                        use_mix=use_mix, months=window.months)
    if not out.get("ok"):
        return out
    out.update({"building_id": str(building_id), "building_name": facts["name"],
                "period_start": window.period_start.isoformat(), "period_end": window.period_end.isoformat(),
                "meters": window.meters})
    if persist:
        out["rating_id"] = await _snapshot(
            session, building_id=building_id, organization_id=facts["organization_id"],
            scheme="ll97", window=window, value=out["emissions_tco2e"], unit="tCO2e",
            limit=out["cap_tco2e"], status=out["status"], detail=out)
        await session.commit()
    return out


async def latest_ratings(
    session: AsyncSession, *, building_ids: list[UUID], scheme: str | None = None
) -> list[dict[str, Any]]:
    """The most recent snapshot per building and scheme."""
    if not building_ids:
        return []
    q = select(EnergyRating).where(EnergyRating.building_id.in_(building_ids)).order_by(
        EnergyRating.building_id, EnergyRating.scheme, EnergyRating.computed_at.desc())
    if scheme:
        q = q.where(EnergyRating.scheme == scheme)
    seen: set[tuple[UUID, str]] = set()
    out = []
    for r in (await session.execute(q)).scalars().all():
        key = (r.building_id, r.scheme)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": str(r.id), "building_id": str(r.building_id), "scheme": r.scheme,
            "value": float(r.value) if r.value is not None else None, "unit": r.unit,
            "limit_value": float(r.limit_value) if r.limit_value is not None else None,
            "status": r.status, "basis": r.basis, "months_of_data": r.months_of_data,
            "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
            "computed_at": r.computed_at.isoformat() if r.computed_at else None,
            "detail": r.detail_json or {},
        })
    return out
