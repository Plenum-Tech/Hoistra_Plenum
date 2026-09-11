"""Building table — one row per site with its energy profile, latest EUI and benchmark.

Backs the frontend Buildings screen. Every row IS a ``plenum_cafm.sites`` row, keyed on
``site_id VARCHAR(50)`` (the table's primary key in this deployment); nothing here is
hard-coded building data. The energy tables key on a UUID, so they are joined by text on
whichever identifier the site row carries. What Feature C knows about each site:

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

import json
import re
from statistics import median
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.energy import BuildingEnergyProfile, EnergyMeter, EuiSnapshot
from ..compliance.site_links import as_uuid, sites_shape
from . import building_rollup
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

#: Names that resolve to a country code. Codes and country names, then sub-national names
#: — a region, emirate or home nation — because the columns this reads are filled by hand
#: and by import, and a person writing "Dubai" under country means the UAE.
#:
#: One rule for adding to the second group: the name must be able to mean exactly ONE
#: country. That is why the seven emirates are here and "Central Region" is not — Singapore
#: has one, and so do Ghana, Uganda and Malawi. The bare compass regions are absent for the
#: same reason: "South West" names a part of a dozen countries, and the two UK sites holding
#: it are not evidence about the phrase. Unmapped names return None, which is the truth.
_COUNTRY_ALIASES = {
    # United Kingdom
    "UK": "UK", "GB": "UK", "GBR": "UK", "UNITED KINGDOM": "UK", "GREAT BRITAIN": "UK",
    "ENGLAND": "UK", "SCOTLAND": "UK", "WALES": "UK", "CYMRU": "UK",
    "NORTHERN IRELAND": "UK", "NORTHERN IRL.": "UK", "NORTHERN IRL": "UK", "NI": "UK",
    "GREATER LONDON": "UK", "LONDON": "UK", "GREATER MANCHESTER": "UK",
    "WEST MIDLANDS": "UK", "YORKSHIRE": "UK", "MERSEYSIDE": "UK",
    "WEST YORKSHIRE": "UK", "SOUTH YORKSHIRE": "UK", "TYNE AND WEAR": "UK",
    # United States
    "US": "US", "USA": "US", "UNITED STATES": "US", "UNITED STATES OF AMERICA": "US",
    "NEW YORK": "US",
    # United Arab Emirates — all seven emirates, each unambiguous.
    "AE": "AE", "UAE": "AE", "ARE": "AE", "UNITED ARAB EMIRATES": "AE",
    "ABU DHABI": "AE", "AJMAN": "AE", "DUBAI": "AE", "FUJAIRAH": "AE",
    "RAS AL KHAIMAH": "AE", "RAS AL-KHAIMAH": "AE", "SHARJAH": "AE",
    "UMM AL QUWAIN": "AE", "UMM AL-QUWAIN": "AE",
    # Singapore
    "SG": "SG", "SGP": "SG", "SINGAPORE": "SG",
}

#: A bare ISO-style code, which is accepted as itself so a country this table has never
#: heard of still works when it arrives already coded.
_ISO_LIKE = re.compile(r"^[A-Z]{2,3}$")

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
    """The country code a name resolves to, or None when it resolves to nothing.

    None is a real answer and the important one. The previous version ended `or s`, which
    made the `else None` branch unreachable and handed back whatever it was given — so any
    place name became a country code simply by being passed in, and no caller could tell a
    resolved code from an unmapped string. `region` now carries Sharjah on 96 production
    sites; deriving country from it would have written SHARJAH as the code.
    """
    s = str(raw or "").strip().upper()
    if not s:
        return None
    code = _COUNTRY_ALIASES.get(s)
    if code:
        return code
    # Already a code: accepted as itself, so a country not in the table still works.
    # Anything else is a name nobody has mapped, and saying so is the whole point.
    return s if _ISO_LIKE.match(s) else None


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


def parse_use_mix(value: Any) -> list[dict[str, Any]]:
    """sites.use_mix (JSONB or its text) -> [{"use": str, "pct": number}]; invalid -> []."""
    if value is None:
        return []
    data = value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return []
    if isinstance(data, dict):
        data = [{"use": k, "pct": v} for k, v in data.items()]
    out: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict):
            use, pct = item.get("use") or item.get("name"), _num(item.get("pct") or item.get("share"))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            use, pct = item[0], _num(item[1])
        else:
            continue
        if use and pct is not None:
            out.append({"use": str(use), "pct": round(pct, 1)})
    return out


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
    key = str(site.get("key") or "").strip()  # sites.site_id VARCHAR(50) — the row's identity
    site_uuid = as_uuid(key) or as_uuid(site.get("alt_id"))  # only used to join the energy tables
    floors = _int(site.get("floors"))
    gfa = _num(site.get("gfa_sqm"))
    cc = country_code_for(site.get("country_code") or site.get("country"))
    pack = dict(BENCHMARK_PACKS.get(cc or "", _FALLBACK_PACK))
    # A standard recorded on the site row overrides the country default (a building can be
    # scored under a scheme its market does not mandate, e.g. a voluntary rating).
    if site.get("benchmark_standard"):
        pack["standard"] = site["benchmark_standard"]
        pack["standing"] = site.get("benchmark_standing") or pack["standing"]
        pack["standing_note"] = site.get("benchmark_standing_note") or pack["standing_note"]
    use_type = site.get("use_type") or site.get("site_type")
    use_mix = parse_use_mix(site.get("use_mix")) or ([{"use": str(use_type), "pct": 100.0}] if use_type else [])

    building_type = (profile or {}).get("building_type") or tm46_type_for(use_type)
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
    # Benchmark precedence: the figure the EUI snapshot was compared against, then the TM46
    # figure on the building's energy profile, then a value recorded on the site row, and
    # only last a TM46 category default matched from the site's use. A recorded figure is a
    # fact about this building; the category default is not. TM46 numbers are only used
    # where TM46 is the market's standard.
    tm46_market = pack["numeric_source"] == "tm46"
    if bench is None and tm46_market and profile and profile.get("tm46_electricity_benchmark") is not None:
        bench = _num(profile.get("tm46_electricity_benchmark"))
        bench_source = "energy_profile"
    if bench is None and _num(site.get("benchmark_kwh_per_m2")) is not None:
        bench = _num(site.get("benchmark_kwh_per_m2"))
        bench_source = "sites_recorded"
    if bench is None and tm46_market and building_type:
        # Compare like with like. A snapshot states the fuel it measured, so its benchmark is
        # that fuel's. A whole-building EUI recorded on the row names no fuel and covers all
        # of them, so it reads against the combined benchmark — scoring a 214 kWh/m²
        # whole-building figure against TM46's 95 electricity-only would report a building
        # sitting AT its benchmark as 125% over it.
        fuel = str((snapshot or {}).get("meter_type") or "").strip().lower() or "combined"
        if fuel not in {"electricity", "gas", "combined"}:
            fuel = "combined"
        bench = tm46_benchmark(building_type, fuel)
        bench_source = f"tm46_{fuel}_by_use" if bench is not None else None

    # EUI: computed from meter readings when there is a snapshot, else the recorded value.
    eui_source = "eui_snapshot" if eui is not None else None
    if eui is None and _num(site.get("eui_kwh_per_m2")) is not None:
        eui = _num(site.get("eui_kwh_per_m2"))
        # buildings.eui_kwh_m2 in the canonical model; sites.eui_kwh_per_m2 in the older one.
        eui_source = "buildings_recorded" if site.get("building_id") or site.get("primary_use") else "sites_recorded"
    if deviation is None and eui is not None and bench:
        deviation = round(100.0 * (eui - bench) / bench, 2)

    met = metering_for(meters or [])
    if not meters and (site.get("metering_route") or site.get("metering_granularity")):
        gran = str(site.get("metering_granularity") or "building-level").lower()
        met = {
            "metering_route": site.get("metering_route"),
            "metering_granularity": gran,
            "metering_inferred": gran != "sub-metered",
            "meters_active": 0,
            "meters_sub": 0,
            "meters_simulated": False,
            "metering_source": "sites_recorded",
        }
    else:
        met["metering_source"] = "energy_meters" if meters else None
    hoist_score = _int(site.get("hoist_score"))

    present = {
        "name": bool(site.get("building_name") or site.get("name")),
        "country": bool(site.get("country") or site.get("country_code")),
        "region": bool(site.get("region") or site.get("city")),
        "site_type": bool(use_type),
        "floors": floors is not None,
        "gfa_sqm": gfa is not None or gia is not None,
        "energy_profile": profile is not None,
        "meters": met["meters_active"] > 0 or bool(met.get("metering_route")),
        "eui": eui is not None,
    }
    missing = [f for f in COMPLETENESS_FIELDS if not present[f]]
    completeness = round(100.0 * (len(COMPLETENESS_FIELDS) - len(missing)) / len(COMPLETENESS_FIELDS))

    row = {
        # The row's identity. When the graph is the root this IS the building_id ("B-001");
        # on the sites fallback both name the same key, so a caller can read either.
        "building_id": key or None,
        "site_id": key or None,
        "site_key": key or (str(site_uuid) if site_uuid else None),
        "site_uuid": str(site_uuid) if site_uuid else None,
        "name": site.get("building_name") or site.get("name") or site.get("code") or key,
        "site_name": site.get("name"),
        "building_name": site.get("building_name"),
        "code": site.get("building_code") or site.get("code"),
        "country": site.get("country") or site.get("country_code"),
        "country_code": cc,
        "city": site.get("city"),
        "region": site.get("region") or site.get("city"),
        "postcode": site.get("postcode"),
        "status": site.get("status"),
        "site_type": site.get("site_type"),
        "use_type": use_type,
        "use_mix": use_mix,
        "floors": floors,
        "gfa_sqm": gfa if gfa is not None else gia,
        "gfa_source": "sites" if gfa is not None else ("energy_profile" if gia is not None else None),
        "building_type": building_type,
        "has_energy_profile": profile is not None,
        "gia_m2": gia,
        "eui_kwh_per_m2": round(eui, 2) if eui is not None else None,
        "eui_source": eui_source,
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
        "hoist_score": hoist_score,
        "record_completeness_pct": completeness,
        "completeness_missing": missing,
        # What a client quotes back as expected_updated_at so a patch cannot silently
        # overwrite an edit made between the read and the write.
        "updated_at": site.get("updated_at"),
        "created_at": site.get("created_at"),
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


def sqft_to_sqm(value: Any) -> float | None:
    """Square feet to square metres. The canonical schema records gross_area_sqft; every
    benchmark on the platform is kWh/m², so the two must never be compared unconverted."""
    v = _num(value)
    return round(v / building_rollup.SQFT_PER_SQM, 2) if v is not None else None


def building_to_row_input(b: dict[str, Any]) -> dict[str, Any]:
    """A plenum_cafm.buildings row in the shape shape_building_row() reads.

    The two tables carry the same facts under different names — a building has `name` where
    a site has `site_name`, and its recorded fallbacks are suffixed `_recorded` so they can
    never be mistaken for a counted figure.
    """
    # Country, region and the benchmark standard come from the building's LOCATION and the
    # regulation pack that location points at. The per-building columns are only read where a
    # deployment has not migrated to locations yet, so nothing regresses mid-migration.
    return {
        "key": b.get("building_id"),
        "building_id": b.get("building_id"),
        "primary_use": b.get("primary_use"),
        "alt_id": b.get("site_id"),
        "name": b.get("name") or b.get("building_name") or b.get("site_name"),
        "building_name": b.get("name") or b.get("building_name"),
        "building_code": b.get("building_code"),
        "code": b.get("building_code"),
        "updated_at": b.get("updated_at"),
        "created_at": b.get("created_at"),
        "country": b.get("country"),
        "country_code": b.get("loc_country_code") or b.get("country_code")
                        or b.get("site_country_code"),
        "city": b.get("city"),
        "region": b.get("loc_region") or b.get("state") or b.get("city")
                  or b.get("site_region") or b.get("site_city"),
        "postcode": b.get("postcode"),
        "status": b.get("status"),
        "site_type": b.get("primary_use") or b.get("use_type"),
        "use_type": b.get("primary_use") or b.get("use_type"),
        "use_mix": None,
        "floors": b.get("floors") if b.get("floors") is not None else b.get("floors_recorded"),
        # The canonical area is square feet; the row model works in m².
        "gfa_sqm": sqft_to_sqm(b.get("gross_area_sqft")) or b.get("gfa_sqm_recorded"),
        # How the reading arrives. Recorded on the site, not the building — the same
        # fallback shape as country/region above.
        "metering_route": b.get("metering_route") or b.get("site_metering_route"),
        "metering_granularity": (b.get("metering_granularity")
                                 or b.get("site_metering_granularity")),
        "benchmark_standard": b.get("pack_standard") or b.get("benchmark_standard"),
        "benchmark_standing": b.get("pack_standing") or b.get("benchmark_standing"),
        "benchmark_standing_note": b.get("pack_standing_note") or b.get("benchmark_standing_note"),
        "benchmark_source_label": b.get("pack_benchmark_source"),
        "eui_kwh_per_m2": b.get("eui_kwh_m2") or b.get("eui_kwh_per_m2"),
        "benchmark_kwh_per_m2": b.get("benchmark_kwh_per_m2"),
        "hoist_score": b.get("hoist_score"),
    }


def apply_graph_rollup(row: dict[str, Any], roll: dict[str, Any] | None) -> dict[str, Any]:
    """Overlay what the graph counted onto a shaped row, and say where each figure came from.

    A counted figure beats a recorded one — the floors on record ARE the floors — but only
    when the count is complete, and a partial count is the normal state of a graph being
    filled in. Three spaces recorded on a 24-storey building sum to a fraction of its area,
    and letting that replace the surveyed figure understated one building here by twelve
    times while looking like an ordinary number.

    So a counted figure replaces a recorded one only when it is not SMALLER than it. When
    it is smaller the survey stands, and the count travels beside it as
    ``gfa_counted_sqm`` / ``floors_counted`` with the shortfall named in ``partial_counts``
    — visibly incomplete rather than quietly wrong. A building with no rows at all keeps
    its surveyed numbers. Each field carries its own source so the two are never conflated.
    """
    row["floors_source"] = "buildings_recorded" if row.get("floors") is not None else None
    row["gfa_source"] = row.get("gfa_source") or ("buildings_recorded" if row.get("gfa_sqm") is not None else None)
    row["use_mix_source"] = "buildings_recorded" if row.get("use_mix") else None
    row["graph_counts"] = {}
    if not roll:
        return row

    partial: list[str] = []

    floors = roll.get("floors")
    if floors:
        recorded = row.get("floors")
        row["floors_counted"] = int(floors)
        if recorded is None or int(floors) >= int(recorded):
            row["floors"] = int(floors)
            row["floors_source"] = "floors_table"
        else:
            # Fewer floor rows than the survey says the building has: the graph is being
            # filled in, not correcting the survey.
            partial.append(f"floors: {int(floors)} of {int(recorded)} on record")

    gfa = roll.get("gfa_sqm")
    if gfa:
        recorded_gfa = row.get("gfa_sqm")
        row["gfa_counted_sqm"] = round(float(gfa), 2)
        if recorded_gfa is None or float(gfa) >= float(recorded_gfa):
            row["gfa_sqm"] = float(gfa)
            row["gfa_source"] = "spaces_sum"
        else:
            partial.append(
                f"area: {round(float(gfa)):,} m² counted of {round(float(recorded_gfa)):,} m² recorded"
            )
    mix = roll.get("use_mix") or []
    if mix:
        row["use_mix"] = mix
        row["use_mix_source"] = "spaces_by_type"
        if roll.get("dominant_use"):
            row["use_type"] = roll["dominant_use"]
            if not row.get("building_type"):
                row["building_type"] = tm46_type_for(roll["dominant_use"])
    row["spaces"] = roll.get("spaces")
    row["graph_counts"] = roll.get("counts") or {}
    # Named rather than merely implied by two fields disagreeing, so a UI can show "the
    # graph knows part of this building" without the reader having to spot it.
    row["partial_counts"] = partial
    return row


def attribute_energy(
    building_id: str,
    site_id: str,
    buildings_on_site: int,
    profiles: dict[str, Any],
    snapshots: dict[str, Any],
    meters: dict[str, Any],
) -> tuple[Any, Any, list[Any], str]:
    """Which energy records belong to this building, and on what basis.

    Energy is recorded against a site. A building's own records always apply. A site's
    records apply only when the building IS the site — one building on it. Where a site
    holds several, its site-level reading is left off every one of them: dividing one
    meter between two buildings would state a split the data does not contain, and showing
    the whole reading against each would count the same kilowatt-hours twice.

    Returns (profile, snapshot, meters, attribution) where attribution is one of
    building | site_sole_building | unattributed_site_shared | none.
    """
    b_prof, b_snap = profiles.get(building_id), snapshots.get(building_id)
    b_mtrs = meters.get(building_id) or []
    if b_prof or b_snap or b_mtrs:
        return b_prof, b_snap, b_mtrs, "building"

    sole = bool(site_id) and buildings_on_site == 1
    if sole:
        s_prof, s_snap = profiles.get(site_id), snapshots.get(site_id)
        s_mtrs = meters.get(site_id) or []
        if s_prof or s_snap or s_mtrs:
            return s_prof, s_snap, s_mtrs, "site_sole_building"
        return None, None, [], "none"

    if site_id and (profiles.get(site_id) or snapshots.get(site_id) or meters.get(site_id)):
        return None, None, [], "unattributed_site_shared"
    return None, None, [], "none"


def _pick(cols: dict[str, str], *names: str) -> str | None:
    return next((c for c in names if c in cols), None)


async def _load_site_rows(session: AsyncSession, *, limit: int) -> list[dict[str, Any]]:
    """Sites as plain dicts, naming only the columns the live table really has."""
    shape = await sites_shape(session)
    cols = shape["columns"]
    if not shape["usable"]:
        return []
    key_col = _pick(cols, "site_id", "id")
    wanted = {
        "key": key_col,
        "alt_id": _pick(cols, "id") if key_col != "id" else _pick(cols, "site_id"),
        "name": shape["name_columns"][0],
        "code": _pick(cols, "site_code", "code", "site_ref"),
        "country": _pick(cols, "country", "country_code"),
        "city": _pick(cols, "city", "town"),
        "region": _pick(cols, "state", "region", "province", "emirate"),
        "postcode": _pick(cols, "postcode", "postal_code", "zip"),
        "status": _pick(cols, "status"),
        "site_type": _pick(cols, "site_type", "building_type", "use_type", "property_type"),
        "floors": _pick(cols, "floors", "floor_count", "num_floors", "storeys"),
        "gfa_sqm": _pick(cols, "gfa_sqm", "gia_m2", "floor_area_sqm", "gross_floor_area", "area_sqm"),
        # Building-table columns added by migrations/sites_building_table_columns.sql. Each is
        # the RECORDED value on the site; computed figures from meters/snapshots win over them.
        "building_name": _pick(cols, "building_name"),
        "building_code": _pick(cols, "building_code"),
        "country_code": _pick(cols, "country_code"),
        "use_type": _pick(cols, "use_type"),
        "use_mix": _pick(cols, "use_mix"),
        "metering_route": _pick(cols, "metering_route"),
        "metering_granularity": _pick(cols, "metering_granularity"),
        "benchmark_standard": _pick(cols, "benchmark_standard"),
        "benchmark_standing": _pick(cols, "benchmark_standing"),
        "benchmark_standing_note": _pick(cols, "benchmark_standing_note"),
        "eui_kwh_per_m2": _pick(cols, "eui_kwh_per_m2"),
        "benchmark_kwh_per_m2": _pick(cols, "benchmark_kwh_per_m2"),
        "hoist_score": _pick(cols, "hoist_score"),
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


async def get_building(
    session: AsyncSession,
    site_id: str,
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """One building by ``sites.site_id`` (VARCHAR(50)); also matches site_uuid or building_code.

    Built from the same rows as the table so the two never disagree; the rolling benchmark
    needs the other buildings, which is why this is not a single-row query.
    """
    table = await list_buildings(session, organization_id=organization_id, limit=5000)
    needle = str(site_id or "").strip()
    for row in table["buildings"]:
        if needle and needle in {row.get("site_id"), row.get("site_uuid"), row.get("code")}:
            return {"ok": True, "building": row, "benchmark_unit": table["benchmark_unit"],
                    "completeness_fields": table["completeness_fields"]}
    return {"ok": False, "error": "building_not_found", "site_id": needle}


async def _list_from_graph(
    session: AsyncSession,
    buildings: list[dict[str, Any]],
    roll: dict[str, Any],
    *,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """The table rooted on plenum_cafm.buildings, with every figure the graph can count.

    A site may hold several buildings. Energy records key on the site, so site-level figures
    are attributed to a building ONLY when that building is the whole site — splitting one
    site's meter across two buildings would invent a division the data does not contain.
    Where a site has more than one building, its site-level energy is left off both and the
    row says the reading is unattributed rather than showing half of it twice.
    """
    profiles, snapshots, meters = await _energy_by_site(session, organization_id)
    per_site: dict[str, int] = {}
    for b in buildings:
        sid = str(b.get("site_id") or "").strip()
        if sid:
            per_site[sid] = per_site.get(sid, 0) + 1

    rows: list[dict[str, Any]] = []
    for b in buildings:
        src = building_to_row_input(b)
        bid = str(b.get("building_id") or "")
        sid = str(b.get("site_id") or "").strip()
        prof, snap, mtrs, attribution = attribute_energy(
            bid, sid, per_site.get(sid, 1) if sid else 1, profiles, snapshots, meters
        )
        row = shape_building_row(src, profile=prof, snapshot=snap, meters=mtrs)
        row["site_id"] = sid or None
        row["buildings_on_site"] = per_site.get(sid, 1) if sid else 1
        row["energy_attribution"] = attribution
        rows.append(apply_graph_rollup(row, roll.get(bid)))
    apply_rolling_benchmarks(rows)
    tm46 = load_tm46()
    return {
        "ok": True,
        "count": len(rows),
        "root": "buildings",
        "graph_shape": roll.get("_shape", {}),
        "benchmark_packs": BENCHMARK_PACKS,
        "tm46_pack": tm46.get("pack"),
        "benchmark_unit": tm46.get("unit") or "kWh/m²/yr",
        "completeness_fields": list(COMPLETENESS_FIELDS),
        "buildings": rows,
    }


async def list_buildings(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    # The building graph is the root when it has rows: a site can hold several buildings, and
    # floors / area / use split are counted from it rather than typed onto a row. A deployment
    # that has not populated plenum_cafm.buildings yet still reads from sites, one per building.
    graph_buildings = await building_rollup.load_buildings(session, limit=limit)
    roll = await building_rollup.rollups(session, limit=limit) if graph_buildings else {}
    if graph_buildings:
        return await _list_from_graph(
            session, graph_buildings, roll, organization_id=organization_id
        )
    sites = await _load_site_rows(session, limit=limit)

    profiles, snapshots, meters = await _energy_by_site(session, organization_id)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _lookup(table: dict[str, Any], site: dict[str, Any]) -> Any:
        # Energy tables key on a UUID site_id; sites here keys on site_id VARCHAR(50) and may
        # carry the UUID in `id`. Match on the text of either, so both shapes join.
        for cand in (site.get("key"), site.get("alt_id")):
            c = str(cand or "").strip()
            if c and c in table:
                return table[c]
            u = as_uuid(c)
            if u is not None and str(u) in table:
                return table[str(u)]
        return None

    for site in sites:
        cands = {str(c).strip() for c in (site.get("key"), site.get("alt_id")) if c}
        cands |= {str(as_uuid(c)) for c in list(cands) if as_uuid(c) is not None}
        rows.append(
            shape_building_row(
                site,
                profile=_lookup(profiles, site),
                snapshot=_lookup(snapshots, site),
                meters=_lookup(meters, site) or [],
            )
        )
        seen |= cands

    # A site with an energy profile but no sites row is still a building with a footprint.
    for sid, p in profiles.items():
        if sid in seen:
            continue
        rows.append(
            shape_building_row(
                {"key": sid, "alt_id": None, "name": None, "site_type": p.get("building_type")},
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
        "root": "sites",
        "sites_table_rows": len(sites),
        "benchmark_packs": BENCHMARK_PACKS,
        "tm46_pack": tm46.get("pack"),
        "benchmark_unit": tm46.get("unit") or "kWh/m²/yr",
        "completeness_fields": list(COMPLETENESS_FIELDS),
        "buildings": rows,
    }


async def _energy_by_site(
    session: AsyncSession, organization_id: UUID | None
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Energy profile, latest EUI snapshot and active meters, keyed by site/building id text."""
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

    return profiles, snapshots, meters
