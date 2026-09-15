"""Benchmark validation: every building in scope, checked against its own market's rule.

The Energy page showed "no EUI reading on record" and dormant country tiles for a
portfolio that had meters reading half-hourly — because nothing ever turned the readings
into an EUI unless somebody POSTed /eui/compute with a hand-picked period and a pre-built
energy profile, and the country tiles read a survey column on the site row that nobody had
filled. The benchmark rules existed; nothing ran them.

This runs them. For each building in scope it derives the EUI from the readings actually
on record (annualised from the months that have data, and saying how many), falls back to
the figure recorded on the row when there are no readings (and says so), applies the
market's own benchmark rule — TM46 by use class (UK), the BCA reference (SG), the rolling
portfolio median (UAE and any market without an operational standard), LL97 / Energy Star
(US, in their own units) — prices the excess in the market's currency, persists the result
as an EUI snapshot so every reader (the buildings table, the country tiles, the market
profiles) sees the same number, and reports, per market, what was validated and exactly
what is still missing for what it could not.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import EuiSnapshot
from ..compliance import epc_rating
from ..compliance.site_links import as_uuid
from . import market_profiles, us_ratings
from .buildings import _ROLLING_MIN_COMPARABLES, BENCHMARK_PACKS, country_code_for, tm46_type_for
from .detectors import currency_for
from .eui import tm46_benchmark
from .ratings_position import BCA_OFFICE_REFERENCE_KWH_M2

log = get_logger(__name__)

#: Fewer months than this and the annualised figure is provisional — shown, but labelled.
PROVISIONAL_BELOW_MONTHS = 3
#: The US ratings need this much to be a position rather than a guess (us_ratings rule).
US_MIN_MONTHS = us_ratings.MIN_MONTHS_TO_SHOW
WINDOW_MONTHS = 12

#: Which rule each market's benchmark comes from. The buildings table's packs name a
#: numeric source only where a kWh/m² table exists on the platform (TM46, the rolling
#: median); the US and Singapore rules are named here so a US building is scored under
#: LL97 / Energy Star and a Singapore one against the BCA reference — never silently under
#: the rolling median because the pack left the field empty.
MARKET_RULE = {"UK": "tm46", "GB": "tm46", "US": "ll97", "SG": "bca", "AE": "portfolio_rolling"}


def _rule_for(cc: str | None) -> str:
    cc = (cc or "").upper()
    if cc in MARKET_RULE:
        return MARKET_RULE[cc]
    pack = BENCHMARK_PACKS.get(cc)
    return (pack or {}).get("numeric_source") or "portfolio_rolling"


def fuel_for(kwh_by_fuel: dict[str, float] | None) -> str:
    """What the readings measured: one fuel, or the whole building. A TM46 benchmark is
    per fuel, so an electricity-only EUI must read against the electricity figure — scoring
    it against the combined one reports a building at its benchmark as far under it."""
    fuels = {f for f, v in (kwh_by_fuel or {}).items() if v and v > 0}
    if fuels == {"electricity"}:
        return "electricity"
    if fuels == {"gas"}:
        return "gas"
    return "combined"


def _tariff(cc: str | None) -> tuple[float | None, float | None, str]:
    """The market's electricity tariff band and currency, from the reference pack.

    A band, because a commercial tariff is a contract range rather than a number: the market
    pack gives 21.0p to 26.0p in the UK and a 20-to-33 fils slab plus a fuel surcharge in the
    UAE. Callers that want one figure take the low end and say so — understating a cost is
    recoverable, and a midpoint presented as a measurement is not.
    """
    markets = (market_profiles.load_profiles() or {}).get("markets") or {}
    m = markets.get((cc or "").upper()) or {}
    band = ((m.get("tariff") or {}).get("electricity")) or {}
    lo, hi = band.get("low"), band.get("high")
    if lo is None:                                    # a pack that still holds a single point
        elec = m.get("elec") or {}
        lo = hi = elec.get("value") if isinstance(elec, dict) else None
    currency = (m.get("cur") or currency_for(cc))
    return ((float(lo) if lo is not None else None),
            (float(hi) if hi is not None else None), currency)


def annualise(total_kwh: float, months: int) -> float:
    """kWh over ``months`` months of data, scaled to a year. Months with no readings are not
    counted as zero use — they are not counted at all, and the result is provisional."""
    months = max(1, int(months))
    return total_kwh * (12.0 / months)


def derive_eui(*, total_kwh: float, months: int, gfa_m2: float | None) -> dict[str, Any] | None:
    if not gfa_m2 or gfa_m2 <= 0 or total_kwh <= 0 or months <= 0:
        return None
    annual = annualise(total_kwh, months)
    return {
        "eui_kwh_per_m2": round(annual / gfa_m2, 2),
        "annual_kwh": round(annual, 0),
        "months_of_data": int(months),
        "provisional": months < PROVISIONAL_BELOW_MONTHS,
    }


def benchmark_for(*, cc: str | None, use: str | None, recorded: float | None,
                  peers: list[float], fuel: str = "combined") -> dict[str, Any]:
    """The market's rule applied to one building. ``peers`` are the other EUIs in the same
    market and scope, for the rolling rule. Returns value, basis and — when there is no
    figure — the reason, so a missing benchmark is explained rather than blank."""
    rule = _rule_for(cc)
    if rule == "tm46":
        bt = tm46_type_for(use)
        value = tm46_benchmark(bt, fuel) if bt else None
        if value is not None:
            return {"value": float(value), "basis": f"tm46_{fuel}_by_use", "building_type": bt, "fuel": fuel}
        if recorded is not None:
            return {"value": float(recorded), "basis": "recorded", "building_type": bt}
        return {"value": None, "basis": None, "reason": "use_type" if not bt else "tm46_no_figure"}
    if rule == "bca":
        if recorded is not None:
            return {"value": float(recorded), "basis": "recorded"}
        return {"value": BCA_OFFICE_REFERENCE_KWH_M2, "basis": "bca_office_reference"}
    if rule == "ll97":
        # The US rule is not a kWh/m² figure. The position is LL97 / Energy Star, computed
        # by us_ratings in their own units; a recorded kWh/m² reference is kept if the row
        # has one, labelled as what it is.
        if recorded is not None:
            return {"value": float(recorded), "basis": "recorded"}
        return {"value": None, "basis": "ll97_energy_star", "reason": "us_rule_not_kwh_m2"}
    # Rolling portfolio median — UAE and any market without an operational standard.
    if len(peers) >= _ROLLING_MIN_COMPARABLES:
        return {"value": round(float(median(peers)), 2), "basis": "portfolio_rolling", "comparables": len(peers)}
    if recorded is not None:
        return {"value": float(recorded), "basis": "recorded", "comparables": len(peers)}
    return {"value": None, "basis": None, "reason": "comparable_buildings", "comparables": len(peers)}


def price_excess(*, eui: float | None, bench: float | None, gfa_m2: float | None,
                 cc: str | None) -> dict[str, Any]:
    if eui is None or bench is None or not gfa_m2:
        return {"deviation_pct": None, "excess_kwh": None, "cost": None, "cost_low": None,
                "cost_high": None, "currency": currency_for(cc), "priced": False}
    lo, hi, currency = _tariff(cc)
    deviation = round(100.0 * (eui - bench) / bench, 1) if bench else None
    excess = round(max(0.0, eui - bench) * gfa_m2, 0)
    cost_low = round(excess * lo, 0) if lo is not None else None
    cost_high = round(excess * hi, 0) if hi is not None else None
    return {
        "deviation_pct": deviation, "excess_kwh": excess,
        # `cost` is the low end of the band, not a midpoint. A single headline figure has to
        # be one or the other, and the conservative end is the one that cannot overstate what
        # a building is costing. cost_high sits beside it for the top of the range.
        "cost": cost_low, "cost_low": cost_low, "cost_high": cost_high,
        "cost_basis": ("low end of the market tariff band" if lo != hi else "flat market rate"),
        "currency": currency, "tariff": lo, "tariff_low": lo, "tariff_high": hi,
        "priced": cost_low is not None,
    }


# ── inputs ───────────────────────────────────────────────────────────────────

async def _facts(session: AsyncSession, building_ids: list[UUID]) -> list[dict[str, Any]]:
    if not building_ids:
        return []
    rows = (await session.execute(text("""
        SELECT b.building_id::text AS building_id, b.name, b.primary_use::text AS primary_use,
               b.gross_area_sqft, b.organization_id::text AS organization_id, b.site_id AS site_key,
               coalesce(s.country_code, b.raw_metadata->>'country_code') AS country_code,
               s.use_type, s.use_mix, s.gfa_sqm, s.eui_kwh_per_m2 AS recorded_eui,
               s.benchmark_kwh_per_m2 AS recorded_benchmark,
               p.building_type AS profile_type, p.gia_m2 AS profile_gia_m2,
               (SELECT count(*) FROM plenum_cafm.energy_meters em WHERE em.active AND em.building_id = b.building_id) AS meters_active
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
          LEFT JOIN plenum_cafm.building_energy_profiles p ON p.building_id = b.building_id
         WHERE b.building_id = ANY(CAST(:ids AS uuid[]))
         ORDER BY b.name
    """), {"ids": [str(b) for b in building_ids]})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        gfa = None
        if d.get("profile_gia_m2"):
            gfa, gfa_source = float(d["profile_gia_m2"]), "energy_profile"
        elif d.get("gross_area_sqft"):
            gfa, gfa_source = float(d["gross_area_sqft"]) / us_ratings.M2_TO_FT2, "buildings_recorded"
        elif d.get("gfa_sqm"):
            try:
                gfa, gfa_source = float(d["gfa_sqm"]), "sites_recorded"
            except (TypeError, ValueError):
                gfa, gfa_source = None, None
        else:
            gfa_source = None
        d["gfa_m2"] = gfa
        d["gfa_source"] = gfa_source
        d["use"] = d.get("profile_type") or d.get("use_type") or d.get("primary_use")
        d["cc"] = country_code_for(d.get("country_code"))
        d["recorded_eui"] = float(d["recorded_eui"]) if d.get("recorded_eui") is not None else None
        d["recorded_benchmark"] = float(d["recorded_benchmark"]) if d.get("recorded_benchmark") is not None else None
        out.append(d)
    return out


async def _latest_snapshot_end(session: AsyncSession, building_id: UUID, *, fuel: str = "combined") -> date | None:
    return (await session.execute(
        select(EuiSnapshot.period_end).where(EuiSnapshot.building_id == building_id, EuiSnapshot.meter_type == fuel)
        .order_by(EuiSnapshot.period_end.desc(), EuiSnapshot.created_at.desc()).limit(1)
    )).scalar_one_or_none()


async def _epc_buildings(session: AsyncSession, organization_id: UUID | None, ids: list[UUID]) -> set[str]:
    if not ids:
        return set()
    try:
        mees = await epc_rating.mees_summary(session, organization_id=organization_id, building_ids=ids)
        return {b["building_id"] for b in mees.get("buildings", [])}
    except Exception as exc:  # noqa: BLE001 — the EPC register is one input, not the report
        log.warning("benchmarks.epc_read_failed", error=str(exc)[:200])
        return set()


async def _chiller_buildings(session: AsyncSession, ids: list[UUID]) -> set[str]:
    if not ids:
        return set()
    try:
        rows = (await session.execute(text(
            "SELECT DISTINCT building_id::text FROM plenum_cafm.chiller_design_specs"
            " WHERE building_id = ANY(CAST(:ids AS uuid[]))"), {"ids": [str(b) for b in ids]})).scalars().all()
        return {str(r) for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.warning("benchmarks.chiller_read_failed", error=str(exc)[:200])
        return set()


# ── the validation ───────────────────────────────────────────────────────────

async def validate(
    session: AsyncSession,
    *,
    building_ids: list[UUID],
    organization_id: UUID | None = None,
    persist: bool = True,
    window_months: int = WINDOW_MONTHS,
    today: date | None = None,
) -> dict[str, Any]:
    """Derive, benchmark, price and (optionally) persist for every building given; report
    per market what is validated and what each unvalidated building still needs."""
    today = today or date.today()
    org = as_uuid(organization_id)
    facts = await _facts(session, building_ids)
    ids = [UUID(f["building_id"]) for f in facts]
    epc_ok = await _epc_buildings(session, org, [b for b, f in zip(ids, facts) if f["cc"] == "UK"])
    chiller_ok = await _chiller_buildings(session, [b for b, f in zip(ids, facts) if f["cc"] == "AE"])

    # Pass 1: consumption and EUI per building.
    for f in facts:
        bid = UUID(f["building_id"])
        window = None
        try:
            window = await us_ratings.consumption_for_building(session, building_id=bid, months=window_months, end=today)
        except Exception as exc:  # noqa: BLE001
            log.warning("benchmarks.consumption_failed", building_id=str(bid), error=str(exc)[:200])
        f["window"] = window
        total = sum(window.kwh_by_fuel.values()) if window else 0.0
        derived = derive_eui(total_kwh=total, months=window.months if window else 0, gfa_m2=f["gfa_m2"]) if window else None
        if derived:
            f.update(derived, eui_source="derived", total_kwh=round(total, 0),
                     period_start=window.period_start, period_end=window.period_end,
                     fuels=sorted(window.kwh_by_fuel), fuel=fuel_for(window.kwh_by_fuel))
        elif f["recorded_eui"] is not None:
            f.update(eui_kwh_per_m2=f["recorded_eui"], eui_source="recorded", months_of_data=0, provisional=False)
        else:
            f.update(eui_kwh_per_m2=None, eui_source=None, months_of_data=window.months if window else 0, provisional=False)

    # Pass 2: benchmarks — the rolling rule needs every other EUI in the market first.
    by_cc: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        by_cc.setdefault(f["cc"] or "??", []).append(f)
    for cc, group in by_cc.items():
        for f in group:
            peers = [g["eui_kwh_per_m2"] for g in group if g is not f and g.get("eui_kwh_per_m2") is not None]
            # A recorded EUI names no fuel and covers all of them; a derived one is the fuel measured.
            f["benchmark"] = benchmark_for(cc=f["cc"], use=f["use"], recorded=f["recorded_benchmark"], peers=peers,
                                           fuel=f.get("fuel") or "combined")
            f["money"] = price_excess(eui=f.get("eui_kwh_per_m2"), bench=f["benchmark"]["value"],
                                      gfa_m2=f["gfa_m2"], cc=f["cc"])

    # Pass 3: US positions in their own units, where there is enough consumption.
    us_positions: dict[str, dict[str, Any]] = {}
    for f in facts:
        if f["cc"] != "US" or not f.get("window") or f["window"].months < US_MIN_MONTHS or not f["gfa_m2"]:
            continue
        bid = UUID(f["building_id"])
        try:
            ll97 = await us_ratings.compute_ll97(session, building_id=bid, months=window_months, persist=persist)
            es = await us_ratings.compute_energy_star(session, building_id=bid, months=window_months, persist=persist)
            us_positions[f["building_id"]] = {"ll97": ll97, "energy_star": es}
        except Exception as exc:  # noqa: BLE001
            log.warning("benchmarks.us_ratings_failed", building_id=str(bid), error=str(exc)[:200])

    # Pass 4: persist the derived positions as snapshots — one per building per window end,
    # so a daily run does not pile up identical rows.
    written = 0
    if persist:
        for f in facts:
            if f.get("eui_source") != "derived":
                continue
            bid = UUID(f["building_id"])
            if await _latest_snapshot_end(session, bid, fuel=f.get("fuel") or "combined") == f["period_end"]:
                continue
            money = f["money"]
            session.add(EuiSnapshot(
                id=uuid4(),
                organization_id=as_uuid(f.get("organization_id")) or org,
                building_id=bid, period_start=f["period_start"], period_end=f["period_end"],
                meter_type=f.get("fuel") or "combined",
                total_kwh=Decimal(str(f["total_kwh"])), gia_m2=Decimal(str(round(f["gfa_m2"], 2))),
                eui_kwh_per_m2=Decimal(str(f["eui_kwh_per_m2"])),
                benchmark_kwh_per_m2=Decimal(str(f["benchmark"]["value"])) if f["benchmark"]["value"] is not None else None,
                deviation_pct=Decimal(str(money["deviation_pct"])) if money["deviation_pct"] is not None else None,
                excess_kwh=Decimal(str(money["excess_kwh"])) if money["excess_kwh"] is not None else None,
                financial_gbp=Decimal(str(money["cost"])) if money["cost"] is not None else None,
                tariff_used=Decimal(str(money["tariff"])) if money.get("tariff") is not None else None,
            ))
            written += 1
        if written:
            await session.commit()

    # The report.
    buildings_out = []
    markets: dict[str, dict[str, Any]] = {}
    for f in facts:
        cc = f["cc"] or "??"
        needs: list[str] = []
        if not f["gfa_m2"]:
            needs.append("floor_area")
        if not f["use"]:
            needs.append("use_type")
        if not f.get("window"):
            needs.append("meter_readings" if f.get("meters_active") else "meters")
        elif f.get("provisional"):
            needs.append(f"more_months_of_readings ({f['months_of_data']} of {PROVISIONAL_BELOW_MONTHS} minimum)")
        if f["benchmark"]["value"] is None and f["benchmark"].get("reason") not in (None, "us_rule_not_kwh_m2"):
            needs.append(f["benchmark"]["reason"])
        if cc == "UK" and f["building_id"] not in epc_ok:
            needs.append("epc_certificate")
        if cc == "AE" and f["building_id"] not in chiller_ok:
            needs.append("chiller_design_specs")
        if cc == "US" and f["building_id"] not in us_positions:
            needs.append(f"twelve_months_consumption (LL97 / Energy Star need {US_MIN_MONTHS}+ months)")
        validated = f.get("eui_kwh_per_m2") is not None and f["benchmark"]["value"] is not None
        row = {
            "building_id": f["building_id"], "name": f["name"], "country_code": f["cc"],
            "use": f["use"], "gfa_m2": round(f["gfa_m2"], 1) if f["gfa_m2"] else None, "gfa_source": f["gfa_source"],
            "meters_active": int(f.get("meters_active") or 0),
            "eui_kwh_per_m2": f.get("eui_kwh_per_m2"), "eui_source": f.get("eui_source"), "fuel": f.get("fuel"),
            "months_of_data": f.get("months_of_data", 0), "provisional": bool(f.get("provisional")),
            "period_start": f.get("period_start").isoformat() if f.get("period_start") else None,
            "period_end": f.get("period_end").isoformat() if f.get("period_end") else None,
            "benchmark_kwh_per_m2": f["benchmark"]["value"], "benchmark_basis": f["benchmark"]["basis"],
            "benchmark_rule": _rule_for(f["cc"]),
            **{k: v for k, v in f["money"].items() if k != "tariff"},
            "standing": ("over" if (f["money"]["deviation_pct"] or 0) > 0 else "within") if validated else "unvalidated",
            "validated": validated, "needs": needs,
            "us_ratings": us_positions.get(f["building_id"]),
        }
        buildings_out.append(row)
        m = markets.setdefault(cc, {"buildings": 0, "validated": 0, "derived": 0, "recorded": 0, "provisional": 0,
                                    "unvalidated": 0, "rule": _rule_for(f["cc"]), "currency": currency_for(f["cc"]),
                                    "excess_cost_per_year": 0.0, "needs": {}})
        m["buildings"] += 1
        m["validated" if validated else "unvalidated"] += 1
        if row["eui_source"] == "derived":
            m["derived"] += 1
        elif row["eui_source"] == "recorded":
            m["recorded"] += 1
        if row["provisional"]:
            m["provisional"] += 1
        if row["cost"]:
            m["excess_cost_per_year"] += float(row["cost"])
        for n in needs:
            m["needs"][n] = m["needs"].get(n, 0) + 1
    for cc, m in markets.items():
        m["excess_cost_per_year"] = round(m["excess_cost_per_year"], 0)
        m["tiles_ready"] = _tiles_ready(cc, m, epc_ok, us_positions, chiller_ok,
                                        [b for b in buildings_out if b["country_code"] == cc])

    return {
        "ok": True,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "window_months": window_months,
        "persisted": persist,
        "snapshots_written": written,
        "buildings": buildings_out,
        "markets": markets,
        "summary": {
            "buildings": len(buildings_out),
            "validated": sum(1 for b in buildings_out if b["validated"]),
            "derived_from_readings": sum(1 for b in buildings_out if b["eui_source"] == "derived"),
            "recorded_only": sum(1 for b in buildings_out if b["eui_source"] == "recorded"),
            "no_eui": sum(1 for b in buildings_out if b["eui_kwh_per_m2"] is None),
        },
    }


def _tiles_ready(cc: str, m: dict[str, Any], epc_ok: set[str], us_positions: dict[str, Any],
                 chiller_ok: set[str], rows: list[dict[str, Any]]) -> dict[str, bool]:
    """Which of the market's ratings-and-duties tiles have the inputs to show a figure."""
    with_eui = [r for r in rows if r["eui_kwh_per_m2"] is not None]
    if cc == "UK":
        return {"mees": any(r["building_id"] in epc_ok for r in rows),
                "epcs_on_file": any(r["building_id"] in epc_ok for r in rows),
                "eui_vs_tm46": m["validated"] > 0}
    if cc == "US":
        return {"ll97": bool(us_positions), "energy_star": bool(us_positions),
                "ll84_filing": True}   # the filing tile reads the register directly; Due is a result
    if cc == "SG":
        return {"bca_submission": True, "eui_vs_bca": m["validated"] > 0, "green_mark": True}
    if cc == "AE":
        return {"eui_vs_rolling": any(r["benchmark_basis"] == "portfolio_rolling" for r in with_eui),
                "chiller_kw_per_rt": any(r["building_id"] in chiller_ok for r in rows)}
    return {"eui_vs_rolling": m["validated"] > 0}
