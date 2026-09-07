"""Building table — one row per site with its energy profile, latest EUI and benchmark.

Backs the frontend Buildings screen. Reads ``plenum_cafm.sites`` through the same
shape-agnostic helpers compliance uses (the table is varchar-keyed in this deployment and
UUID-keyed in others), then joins what Feature C knows about each site:

* ``building_energy_profiles`` — GIA and TM46 building type (keyed by site UUID)
* ``eui_snapshots`` — the latest annualised EUI and the benchmark it was compared against
* ``energy_meters`` — how the reading arrives and at what granularity

Use drives the benchmark, and the country drives the standard: a UK building reads
against CIBSE TM46, a US one against Energy Star / ASHRAE 100, Singapore against the BCA
Benchmarking Report, and a market with no operational standard against a rolling benchmark
of comparable buildings in the portfolio. Every row says which standard, its legal
standing (so a proposal is never read as a duty), where the benchmark number came from,
how the meter reading arrives, and whether attribution is measured or inferred.
"""
from __future__ import annotations

from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import BuildingEnergyProfile, EnergyMeter, EuiSnapshot
from ..compliance.site_links import as_uuid, sites_shape
from .eui import load_tm46, tm46_benchmark

log = get_logger(__name__)

# Free-text site types seen on sites rows → TM46 category keys. Anything unmapped gets no
# benchmark rather than a wrong one.
_TM46_TYPE_MAP = {
    "office": "office",
    "commercial": "office",
    "offices": "office",
    "mixed": "office",
    "residential": "residential",
    "apartments": "residential",
    "retail": "retail",
    "mall": "retail",
    "shopping": "retail",
    "hospital": "hospital",
    "healthcare": "hospital",
    "hotel": "hotel",
    "hospitality": "hotel",
    "school": "school",
    "education": "school",
    "university": "university",
    "warehouse": "warehouse",
    "logistics": "warehouse",
    "industrial": "industrial",
    "factory": "industrial",
    "sports": "sports",
    "leisure": "sports",
}

# The regulation pack each market benchmarks against, with the legal standing of that
# standard. `standing` is one of: guidance | enacted | mandatory_submission | none.
BENCHMARK_PACKS: dict[str, dict[str, Any]] = {
    "UK": {
        "standard": "CIBSE TM46",
        "standing": "guidance",
        "standing_note": "guidance · EPC E law, EPC B proposed 2031",
        "numeric_source": "tm46",
    },
    "US": {
        "standard": "Energy Star Portfolio Manager · ASHRAE 100",
        "standing": "enacted",
        "standing_note": "enacted, city-scoped · NYC LL97",
        "numeric_source": None,  # no Portfolio Manager reference table on this platform yet
    },
    "SG": {
        "standard": "BCA Benchmarking Report",
        "standing": "mandatory_submission",
        "standing_note": "submission mandatory · rating voluntary",
        "numeric_source": None,
    },
    "AE": {
        "standard": "Rolling portfolio benchmark",
        "standing": "none",
        "standing_note": "no operational standard · comparable buildings in the portfolio",
        "numeric_source": "portfolio_rolling",
    },
}
_FALLBACK_PACK = {
    "standard": "Rolling portfolio benchmark",
    "standing": "none",
    "standing_note": "no regulation pack for this market · comparable buildings in the portfolio",
    "numeric_source": "portfolio_rolling",
}
_ROLLING_MIN_COMPARABLES = 2

_COUNTRY_ALIASES = {
    "UK": "UK", "GB": "UK", "GBR": "UK", "UNITED KINGDOM": "UK", "ENGLAND": "UK",
    "SCOTLAND": "UK", "WALES": "UK", "NORTHERN IRELAND": "UK", "GREAT BRITAIN": "UK",
    "US": "US", "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "AE": "AE", "UAE": "AE", "ARE": "AE", "UNITED ARAB EMIRATES": "AE", "DUBAI": "AE",
    "ABU DHABI": "AE",
    "SG": "SG", "SGP": "SG", "SINGAPORE": "SG",
}

# Fields that make a building row usable on the dashboard. Completeness is the share of
# these present — a data-quality figure, not a compliance or performance score.
COMPLETENESS_FIELDS = (
    "name",
    "country",
    "region",
    "site_type",
    "floors",
    "gfa_sqm",
    "energy_profile",
    "meters",
    "eui",
)


def country_code_for(raw: Any) -> str | None:
    s = str(raw or "").strip().upper()
    if not s:
        return None
    return _COUNTRY_ALIASES.get(s, s if len(s) <= 3 else None) or s


def tm46_type_for(site_type: Any) -> str | None:
    """Map a free-text site type to a TM46 category key, or None when nothing matches."""
    s = str(site_type or "").strip().lower()
    if not s:
        return None
    if s in _TM46_TYPE_MAP:
        return _TM46_TYPE_MAP[s]
    for word, key in _TM46_TYPE_MAP.items():
        if word in s:
            return key
    return None


def metering_for(meters: list[dict[str, Any]]) -> dict[str, Any]:
    """How the site's reading arrives and at what granularity, from its active meters.

    Route precedence per meter: an explicit ``raw_metadata.route`` wins; a DCC device id
    means SMETS2 through the DCC (Smart Energy Code intermediary); an MPAN/MPRN without one
    is a half-hourly data collector under Letter of Authority; ``raw_metadata.simulate``
    marks a demo feed so a simulated reading is never presented as a real one.
    """
    if not meters:
        return {
            "metering_route": None,
            "metering_granularity": "none",
            "metering_inferred": None,
            "meters_active": 0,
            "meters_sub": 0,
            "meters_simulated": False,
        }
    routes: list[str] = []
    simulated = False
    sub = 0
    for m in meters:
        meta = m.get("raw_metadata") or {}
        if meta.get("simulate"):
            simulated = True
        if m.get("is_sub_meter"):
            sub += 1
        r = meta.get("route") or meta.get("source")
        if not r:
            if m.get("dcc_device_id"):
                r = "SMETS2 · SEC intermediary (DCC)"
            elif m.get("mpan") or m.get("mprn"):
                r = "HH data collector · LoA"
            else:
                r = "Own sub-meters + BMS" if m.get("is_sub_meter") else "Meter on record · route not stated"
        if r not in routes:
            routes.append(str(r))
    granularity = "sub-metered" if sub >= 1 and len(meters) >= 2 else "building-level"
    return {
        "metering_route": " · ".join(routes) if routes else None,
        "metering_granularity": granularity,
        # Building-level metering attributes consumption to plant by inference, not measurement.
        "metering_inferred": granularity != "sub-metered",
        "meters_active": len(meters),
        "meters_sub": sub,
        "meters_simulated": simulated,
    }


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        v = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return v if v == v else None  # NaN guard


def _int(value: Any) -> int | None:
    v = _num(value)
    return int(round(v)) if v is not None else None


def shape_building_row(
    site: dict[str, Any],
    *,
    profile: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    meters: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure: one sites row plus what energy knows about it → the API row.

    ``site`` carries plain keys (key, alt_id, name, code, country, city, region, postcode,
    status, site_type, floors, gfa_sqm); missing ones are None. The rolling portfolio
    benchmark is filled in afterwards by ``apply_rolling_benchmarks`` because it needs the
    other rows.
    """
    site_uuid = as_uuid(site.get("key")) or as_uuid(site.get("alt_id"))
    key = str(site.get("key") or "").strip()
    floors = _int(site.get("floors"))
    gfa = _num(site.get("gfa_sqm"))
    cc = country_code_for(site.get("country"))
    pack = BENCHMARK_PACKS.get(cc or "", _FALLBACK_PACK)

    building_type = (profile or {}).get("building_type") or tm46_type_for(site.get("site_type"))
    gia = (profile or {}).get("gia_m2")
    gia = float(gia) if gia is not None else None

    eui = None
    bench = None
    bench_source = None
    deviation = None
    if snapshot:
        eui = _num(snapshot.get("eui_kwh_per_m2"))
        bench = _num(snapshot.get("benchmark_kwh_per_m2"))
        deviation = _num(snapshot.get("deviation_pct"))
        if bench is not None:
            bench_source = "eui_snapshot"
    # A numeric TM46 figure is only a benchmark where TM46 is the market's standard.
    if bench is None and pack["numeric_source"] == "tm46":
        if profile and profile.get("tm46_electricity_benchmark") is not None:
            bench = _num(profile.get("tm46_electricity_benchmark"))
            bench_source = "energy_profile"
        elif building_type:
            bench = tm46_benchmark(building_type, "electricity")
            bench_source = "tm46_by_site_type" if bench is not None else None
    if deviation is None and eui is not None and bench:
        deviation = round(100.0 * (eui - bench) / bench, 2)

    met = metering_for(meters or [])

    present = {
        "name": bool(site.get("name")),
        "country": bool(site.get("country")),
        "region": bool(site.get("region") or site.get("city")),
        "site_type": bool(site.get("site_type")),
        "floors": floors is not None,
        "gfa_sqm": gfa is not None or gia is not None,
        "energy_profile": profile is not None,
        "meters": met["meters_active"] > 0,
        "eui": eui is not None,
    }
    missing = [f for f in COMPLETENESS_FIELDS if not present[f]]
    completeness = round(100.0 * (len(COMPLETENESS_FIELDS) - len(missing)) / len(COMPLETENESS_FIELDS))

    row = {
        "site_key": key or (str(site_uuid) if site_uuid else None),
        "site_uuid": str(site_uuid) if site_uuid else None,
        "site_ref": None if site_uuid and key == str(site_uuid) else (key or None),
        "name": site.get("name") or site.get("code") or key,
        "code": site.get("code"),
        "country": site.get("country"),
        "country_code": cc,
        "city": site.get("city"),
        "region": site.get("region") or site.get("city"),
        "postcode": site.get("postcode"),
        "status": site.get("status"),
        "site_type": site.get("site_type"),
        "floors": floors,
        "gfa_sqm": gfa if gfa is not None else gia,
        "gfa_source": "sites" if gfa is not None else ("energy_profile" if gia is not None else None),
        "building_type": building_type,
        "has_energy_profile": profile is not None,
        "gia_m2": gia,
        "eui_kwh_per_m2": round(eui, 2) if eui is not None else None,
        "eui_period_start": (snapshot or {}).get("period_start"),
        "eui_period_end": (snapshot or {}).get("period_end"),
        "eui_meter_type": (snapshot or {}).get("meter_type"),
        "benchmark_standard": pack["standard"],
        "benchmark_standing": pack["standing"],
        "benchmark_standing_note": pack["standing_note"],
        "benchmark_kwh_per_m2": round(bench, 2) if bench is not None else None,
        "benchmark_source": bench_source,
        "benchmark_comparables": None,
        "deviation_pct": round(deviation, 1) if deviation is not None else None,
        "record_completeness_pct": completeness,
        "completeness_missing": missing,
    }
    row.update(met)
    return row


def apply_rolling_benchmarks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill the rolling portfolio benchmark where the market has no operational standard.

    The benchmark is the median annualised EUI of the OTHER buildings of the same building
    type that have a reading, portfolio-wide (a Dubai office reads against every office
    with a meter, because that is the only comparison available to it). Needs at least
    ``_ROLLING_MIN_COMPARABLES`` comparables; otherwise the row keeps no benchmark and says so.
    """
    for r in rows:
        if r.get("benchmark_kwh_per_m2") is not None:
            continue
        pack = BENCHMARK_PACKS.get(r.get("country_code") or "", _FALLBACK_PACK)
        if pack["numeric_source"] != "portfolio_rolling":
            continue
        bt = r.get("building_type")
        if not bt:
            continue
        peers = [
            o["eui_kwh_per_m2"]
            for o in rows
            if o is not r and o.get("building_type") == bt and o.get("eui_kwh_per_m2") is not None
        ]
        if len(peers) < _ROLLING_MIN_COMPARABLES:
            continue
        bench = round(float(median(peers)), 2)
        r["benchmark_kwh_per_m2"] = bench
        r["benchmark_source"] = "portfolio_rolling"
        r["benchmark_comparables"] = len(peers)
        if r.get("eui_kwh_per_m2") is not None and bench:
            r["deviation_pct"] = round(100.0 * (r["eui_kwh_per_m2"] - bench) / bench, 1)
    return rows


def _pick(cols: dict[str, str], *names: str) -> str | None:
    return next((c for c in names if c in cols), None)


async def _load_site_rows(session: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    """Sites as plain dicts, naming only the columns the live table really has."""
    shape = await sites_shape(session)
    cols = shape["columns"]
    if not shape["usable"]:
        return []
    wanted = {
        "key": shape["key_columns"][0],
        "alt_id": _pick(cols, "id") if shape["key_columns"][0] != "id" else None,
        "name": shape["name_columns"][0],
        "code": _pick(cols, "site_code", "code", "site_ref"),
        "country": _pick(cols, "country", "country_code"),
        "city": _pick(cols, "city", "town"),
        "region": _pick(cols, "region", "state", "province", "emirate"),
        "postcode": _pick(cols, "postcode", "postal_code", "zip"),
        "status": _pick(cols, "status"),
        "site_type": _pick(cols, "site_type", "building_type", "use_type", "property_type"),
        "floors": _pick(cols, "floors", "floor_count", "num_floors", "storeys"),
        "gfa_sqm": _pick(cols, "gfa_sqm", "gia_m2", "floor_area_sqm", "gross_floor_area", "area_sqm"),
    }
    select_parts = [f"{c}::text AS {alias}" for alias, c in wanted.items() if c]
    order = wanted["name"]
    sql = f"SELECT {', '.join(select_parts)} FROM plenum_cafm.sites ORDER BY {order} NULLS LAST LIMIT {int(limit)}"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).mappings().all()
    except Exception as exc:  # noqa: BLE001 — a missing table must not 500 the dashboard
        log.warning("energy.buildings.sites_read_failed", error=str(exc)[:200])
        return []
    out = []
    for r in rows:
        d = {alias: None for alias in wanted}
        d.update({k: (str(v).strip() if v is not None else None) for k, v in dict(r).items()})
        out.append(d)
    return out


async def list_buildings(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    sites = await _load_site_rows(session, limit=limit)

    pq = select(BuildingEnergyProfile)
    if organization_id:
        pq = pq.where(BuildingEnergyProfile.organization_id == organization_id)
    profiles: dict[str, dict[str, Any]] = {}
    try:
        async with session.begin_nested():
            for p in (await session.execute(pq)).scalars().all():
                profiles[str(p.site_id)] = {
                    "building_type": p.building_type,
                    "gia_m2": float(p.gia_m2) if p.gia_m2 is not None else None,
                    "tm46_electricity_benchmark": (
                        float(p.tm46_electricity_benchmark)
                        if p.tm46_electricity_benchmark is not None
                        else None
                    ),
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.buildings.profiles_read_failed", error=str(exc)[:200])

    # Latest snapshot per site: ordered newest first, first hit per site wins.
    sq = select(EuiSnapshot).order_by(
        EuiSnapshot.site_id, EuiSnapshot.period_end.desc(), EuiSnapshot.created_at.desc()
    )
    if organization_id:
        sq = sq.where(EuiSnapshot.organization_id == organization_id)
    snapshots: dict[str, dict[str, Any]] = {}
    try:
        async with session.begin_nested():
            for s in (await session.execute(sq)).scalars().all():
                sid = str(s.site_id)
                if sid in snapshots:
                    continue
                snapshots[sid] = {
                    "eui_kwh_per_m2": float(s.eui_kwh_per_m2),
                    "benchmark_kwh_per_m2": (
                        float(s.benchmark_kwh_per_m2) if s.benchmark_kwh_per_m2 is not None else None
                    ),
                    "deviation_pct": float(s.deviation_pct) if s.deviation_pct is not None else None,
                    "period_start": s.period_start.isoformat() if s.period_start else None,
                    "period_end": s.period_end.isoformat() if s.period_end else None,
                    "meter_type": s.meter_type,
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.buildings.snapshots_read_failed", error=str(exc)[:200])

    meters: dict[str, list[dict[str, Any]]] = {}
    try:
        async with session.begin_nested():
            mq = select(EnergyMeter).where(EnergyMeter.active.is_(True), EnergyMeter.site_id.is_not(None))
            for m in (await session.execute(mq)).scalars().all():
                meters.setdefault(str(m.site_id), []).append(
                    {
                        "meter_type": m.meter_type,
                        "mpan": m.mpan,
                        "mprn": m.mprn,
                        "dcc_device_id": m.dcc_device_id,
                        "is_sub_meter": bool(m.is_sub_meter),
                        "raw_metadata": m.raw_metadata or {},
                    }
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("energy.buildings.meters_read_failed", error=str(exc)[:200])

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for site in sites:
        site_uuid = as_uuid(site.get("key")) or as_uuid(site.get("alt_id"))
        sid = str(site_uuid) if site_uuid else None
        rows.append(
            shape_building_row(
                site,
                profile=profiles.get(sid) if sid else None,
                snapshot=snapshots.get(sid) if sid else None,
                meters=meters.get(sid, []) if sid else [],
            )
        )
        if sid:
            seen.add(sid)

    # A site with an energy profile but no sites row is still a building with a footprint.
    for sid, p in profiles.items():
        if sid in seen:
            continue
        rows.append(
            shape_building_row(
                {"key": sid, "name": None, "site_type": p.get("building_type")},
                profile=p,
                snapshot=snapshots.get(sid),
                meters=meters.get(sid, []),
            )
        )

    apply_rolling_benchmarks(rows)

    tm46 = load_tm46()
    return {
        "ok": True,
        "count": len(rows),
        "sites_table_rows": len(sites),
        "benchmark_packs": BENCHMARK_PACKS,
        "tm46_pack": tm46.get("pack"),
        "benchmark_unit": tm46.get("unit") or "kWh/m²/yr",
        "completeness_fields": list(COMPLETENESS_FIELDS),
        "buildings": rows,
    }
