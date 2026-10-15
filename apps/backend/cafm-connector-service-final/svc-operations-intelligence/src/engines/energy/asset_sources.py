"""The data behind an investigation's bms_trend, utility_bill and weather lines.

Each function here is both an endpoint's answer and the source of the walk's one-liner, so the
line a person reads in the dock and the dataset the orchestrator summarises can never disagree.
Everything here reads; nothing writes.

Three habits every query keeps, each learned the hard way on 28 Sep 2026:

- casts are ``CAST(:p AS type)``. ``:p::type`` is not a bind to text()'s parser, the colon
  reaches Postgres, and the query fails on every call.
- window bounds are ``CAST(:since AS timestamptz)``. hoistra_test holds some timestamps
  without a time zone, and asyncpg refuses an aware datetime typed from such a column.
- a failed query is ``None``, never ``[]``, and the dataset says ``unreadable``. An empty
  answer and a broken one are different facts.
"""
from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import open_meteo
from .meter_scope import counted_meters

log = get_logger(__name__)

#: Last year's window is the same period only with at least this share of this year's days.
COVERAGE_MIN = 0.9
#: Degree-day base temperature — the weather_degree_days table's own default.
BASE_TEMP_C = 15.5
#: Raw BMS readings read for one asset's window. Eight weeks of hourly readings across a dozen
#: types is ~16,000; past this the dataset says it was cut rather than summarising a part. The
#: NEWEST are kept: the latest reading of each type is what the out-of-band rule grades.
MAX_READINGS = 50_000
#: How long the weather lookup may take in all — geocode, fallback geocode, archive. Past it
#: the source is unreadable rather than the whole investigation timing out behind it.
WEATHER_BUDGET_S = 12.0

BILL_BASIS = ("metered — there is no utility-bill register on this platform, so the meter "
              "stands in for the bill")


# ── shared helpers ───────────────────────────────────────────────────────────────────

def num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def a_year_before(d: datetime) -> datetime:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # 29 February
        return d.replace(year=d.year - 1, day=28)


def _day_before_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _pct(now: float | None, then: float | None) -> float | None:
    if now is None or not then:
        return None
    return round((now - then) / then * 100, 1)


async def read_rows(session: AsyncSession, sql: str, params: dict, what: str) -> list[dict] | None:
    """One source, in its own savepoint: its rows, or ``None`` when the query failed."""
    try:
        async with session.begin_nested():
            return [dict(r) for r in
                    (await session.execute(text(sql), params)).mappings().all()]
    except Exception as exc:  # noqa: BLE001 — a source that fails reports itself unreadable
        log.warning("investigate.source_failed", source=what, error=str(exc)[:220])
        return None


async def _release(session: AsyncSession) -> None:
    """End the read transaction so no lock is held across an external wait."""
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — ending a read-only transaction must not end the read
        log.warning("energy.release_failed", error=str(exc)[:160])


def _unreadable(what: str) -> dict[str, Any]:
    return {"status": "unreadable",
            "detail": f"the {what} query failed, so this source says nothing either way"}


def bands_from_readings(readings: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """The band set for each reading type, as asset_intelligence already resolved them."""
    out: dict[str, dict[str, Any]] = {}
    for r in readings or []:
        t = r.get("reading_type")
        if t and (r.get("band_lo") is not None or r.get("band_hi") is not None):
            out[t] = {"lo": num(r.get("band_lo")), "hi": num(r.get("band_hi")),
                      "unit": r.get("unit"), "note": r.get("note")}
    return out


# ── BMS trend ────────────────────────────────────────────────────────────────────────

_CHILLER_DAILY = """
    SELECT date_trunc('day', reading_at) AS day, count(*) AS n,
           avg(CASE WHEN cooling_load_rt > 0 THEN kw_input / cooling_load_rt END) AS kw_per_rt,
           avg(ambient_c) AS ambient_c
      FROM plenum_cafm.chiller_performance_readings
     WHERE asset_id::text = :aid AND reading_at >= CAST(:since AS timestamptz)
     GROUP BY 1 ORDER BY 1"""

_CHILLER_DESIGN = """
    SELECT design_kw_per_rt, design_capacity_rt, design_ambient_c, source
      FROM plenum_cafm.chiller_design_specs WHERE asset_id::text = :aid LIMIT 1"""

_READINGS = """
    SELECT reading_type, value, unit, recorded_at
      FROM plenum_cafm.asset_readings
     WHERE asset_id::text = :aid AND recorded_at >= CAST(:since AS timestamptz)
     ORDER BY recorded_at DESC
     LIMIT :lim"""


def _state(value: float | None, band: dict[str, Any] | None) -> str:
    if value is None or not band or (band.get("lo") is None and band.get("hi") is None):
        return "unknown"
    lo, hi = band.get("lo"), band.get("hi")
    return "out_of_band" if (lo is not None and value < lo) or (hi is not None and value > hi) \
        else "in_band"


async def bms_trend(session: AsyncSession, *, asset_id: str, since: datetime,
                    bands: dict[str, dict[str, Any]], until: datetime | None = None) -> dict[str, Any]:
    """The asset's BMS record over the window: chiller kW/RT when it has one, else its readings."""
    until = until or datetime.now(timezone.utc)
    window = {"since": iso(since), "until": iso(until), "weeks": round((until - since).days / 7)}
    chiller = await read_rows(session, _CHILLER_DAILY, {"aid": str(asset_id), "since": since},
                              "chiller_daily")
    if chiller is None:
        return {**_unreadable("chiller performance"), "window": window}
    if any(int(r.get("n") or 0) for r in chiller):
        design = await read_rows(session, _CHILLER_DESIGN, {"aid": str(asset_id)}, "chiller_design")
        spec = num(design[0].get("design_kw_per_rt")) if design else None
        series = [{"day": str(iso(r["day"]))[:10],
                   "kw_per_rt": round(num(r["kw_per_rt"]), 3) if num(r.get("kw_per_rt")) is not None else None,
                   "ambient_c": round(num(r["ambient_c"]), 1) if num(r.get("ambient_c")) is not None else None,
                   "n": int(r.get("n") or 0)} for r in chiller]
        return {"status": "found", "kind": "chiller_performance", "window": window,
                "points": sum(x["n"] for x in series), "design_kw_per_rt": spec, "series": series,
                "detail": "daily mean kW per RT from the chiller's performance readings"}
    return await asset_readings_trend(session, asset_id=asset_id, since=since, bands=bands,
                                      until=until)


async def asset_readings_trend(session: AsyncSession, *, asset_id: str, since: datetime,
                               bands: dict[str, dict[str, Any]],
                               until: datetime | None = None) -> dict[str, Any]:
    """The asset's own BMS readings as daily buckets, each graded against its band.

    A type with no band is ungraded ("unknown"), never "in band": nothing is graded against a
    limit that was never set.
    """
    until = until or datetime.now(timezone.utc)
    window = {"since": iso(since), "until": iso(until), "weeks": round((until - since).days / 7)}
    raw = await read_rows(session, _READINGS,
                          {"aid": str(asset_id), "since": since, "lim": MAX_READINGS},
                          "asset_readings")
    if raw is None:
        return {**_unreadable("BMS readings"), "kind": "asset_readings", "window": window}

    by_type: dict[str, list[tuple[Any, float | None, Any]]] = defaultdict(list)
    units: dict[str, Any] = {}
    for r in raw:
        t = r.get("reading_type")
        if not t:
            continue
        by_type[t].append((r.get("recorded_at"), num(r.get("value")), r.get("unit")))
        units.setdefault(t, r.get("unit"))
    if not by_type:
        return {"status": "not_found", "kind": "asset_readings", "window": window, "points": 0,
                "detail": ("no chiller performance reading and no BMS reading is on record for "
                           f"this asset in the last {window['weeks']} weeks")}

    types, out_of_band = [], []
    for t in sorted(by_type):
        band = bands.get(t)
        buckets: dict[date, list[float]] = defaultdict(list)
        oob_days: dict[date, int] = defaultdict(int)
        oob_points = 0
        for at, v, _u in by_type[t]:
            d = _day(at)
            if d is None or v is None:
                continue
            buckets[d].append(v)
            if _state(v, band) == "out_of_band":
                oob_days[d] += 1
                oob_points += 1
        days = [{"day": d.isoformat(), "min": round(min(vs), 3), "mean": round(sum(vs) / len(vs), 3),
                 "max": round(max(vs), 3), "n": len(vs), "out_of_band": oob_days.get(d, 0)}
                for d, vs in sorted(buckets.items())]
        last_at, last_v, _ = max(by_type[t], key=lambda x: str(iso(x[0])))
        latest = {"value": last_v, "at": iso(last_at), "state": _state(last_v, band)}
        entry = {"reading_type": t, "unit": units.get(t) or (band or {}).get("unit"),
                 "band": ({"lo": band.get("lo"), "hi": band.get("hi"), "note": band.get("note")}
                          if band else None),
                 "points": len(by_type[t]), "out_of_band_points": oob_points,
                 "days_out_of_band": sum(1 for n in oob_days.values() if n), "days": days,
                 "latest": latest}
        types.append(entry)
        if latest["state"] == "out_of_band":
            out_of_band.append({"reading_type": t, "value": last_v, "unit": entry["unit"],
                                "band_lo": band.get("lo"), "band_hi": band.get("hi"),
                                "at": latest["at"], "days_out_of_band": entry["days_out_of_band"],
                                "days": len(days)})

    stamps = [str(iso(r.get("recorded_at"))) for r in raw if r.get("recorded_at") is not None]
    return {
        "status": "found", "kind": "asset_readings", "window": window,
        "points": sum(t["points"] for t in types), "reading_types": len(types),
        "graded_types": sum(1 for t in types if t["band"]),
        "types": types, "out_of_band": out_of_band,
        "first_at": min(stamps) if stamps else None, "last_at": max(stamps) if stamps else None,
        "truncated": len(raw) >= MAX_READINGS,
        "detail": ("no chiller performance record, so these are the asset's own BMS readings; "
                   "each type is graded against its band, and a type with no band is left "
                   "ungraded rather than called in band"),
    }


# ── utility bill (metered) ───────────────────────────────────────────────────────────

# The meters a building's kWh is summed from are the platform's one predicate (meter_scope):
# the bill line then agrees with the building's EUI and its cost card on what the building is.
_BILL_DAYS = f"""
    SELECT m.id::text AS meter_id,
           coalesce(to_jsonb(m)->>'meter_ref', to_jsonb(m)->>'meter_code', m.id::text) AS meter_ref,
           lower(coalesce(to_jsonb(m)->>'meter_type', 'unknown')) AS fuel,
           coalesce(m.is_sub_meter, false) AS sub,
           to_jsonb(m)->>'tariff_gbp_per_kwh' AS tariff,
           CASE WHEN r.reading_at >= CAST(:since AS timestamptz) THEN 'now' ELSE 'last_year' END
               AS period,
           date_trunc('day', r.reading_at) AS day,
           sum(r.consumption_kwh) AS kwh
      FROM plenum_cafm.energy_meters m
      JOIN plenum_cafm.meter_readings r ON r.meter_id = m.id
     WHERE m.active AND m.building_id = :bid AND {counted_meters("m")}
       AND ((r.reading_at >= CAST(:since AS timestamptz) AND r.reading_at < CAST(:until AS timestamptz))
         OR (r.reading_at >= CAST(:ly_start AS timestamptz) AND r.reading_at < CAST(:ly_end AS timestamptz)))
     GROUP BY 1, 2, 3, 4, 5, 6, 7"""

_BILL_FIRST = f"""
    SELECT m.id::text AS meter_id, min(r.reading_at) AS first_at
      FROM plenum_cafm.energy_meters m
      JOIN plenum_cafm.meter_readings r ON r.meter_id = m.id
     WHERE m.active AND m.building_id = :bid AND {counted_meters("m")}
     GROUP BY 1"""


def _period_totals(rows: list[dict], since_d: date, ly_d: date, n_weeks: int) -> dict[str, Any]:
    """This period against the same weeks last year, compared on the days BOTH have on record.

    Raw totals over two different sets of days measure the gap in the feed, not the building:
    five missing days last year read as a +10% rise, three weeks down this year as a -37% fall,
    and a fuel metered only this year inflated the pooled total. The change is taken over the
    (meter, day) pairs present in both windows, and the two windows count as comparable only
    when those shared pairs cover at least COVERAGE_MIN of each side.
    """
    now_kwh = sum(num(r["kwh"]) or 0 for r in rows if r["period"] == "now")
    ly_kwh = sum(num(r["kwh"]) or 0 for r in rows if r["period"] == "last_year")
    now_days = {_day(r["day"]) for r in rows if r["period"] == "now"}
    ly_days = {_day(r["day"]) for r in rows if r["period"] == "last_year"}
    now_md: dict[tuple, float] = defaultdict(float)
    ly_md: dict[tuple, float] = defaultdict(float)
    for r in rows:
        d = _day(r["day"])
        if d is None:
            continue
        if r["period"] == "now":
            now_md[(r["meter_id"], (d - since_d).days)] += num(r["kwh"]) or 0
        else:
            ly_md[(r["meter_id"], (d - ly_d).days)] += num(r["kwh"]) or 0
    common = now_md.keys() & ly_md.keys()
    now_same = sum(now_md[k] for k in common)
    ly_same = sum(ly_md[k] for k in common)
    comparable = (bool(common) and ly_same > 0
                  and len(common) >= COVERAGE_MIN * max(len(now_md), len(ly_md)))
    weeks = []
    for i in range(n_weeks):
        lo_n, lo_l = since_d + timedelta(days=7 * i), ly_d + timedelta(days=7 * i)
        in_n = [r for r in rows if r["period"] == "now" and lo_n <= _day(r["day"]) < lo_n + timedelta(days=7)]
        in_l = [r for r in rows if r["period"] == "last_year" and lo_l <= _day(r["day"]) < lo_l + timedelta(days=7)]
        weeks.append({"week_start": lo_n.isoformat(), "last_year_week_start": lo_l.isoformat(),
                      "now_kwh": round(sum(num(r["kwh"]) or 0 for r in in_n), 1),
                      "last_year_kwh": round(sum(num(r["kwh"]) or 0 for r in in_l), 1),
                      "now_days": len({_day(r["day"]) for r in in_n}),
                      "last_year_days": len({_day(r["day"]) for r in in_l})})
    return {"now_kwh": round(now_kwh, 1), "last_year_kwh": round(ly_kwh, 1),
            "now_days": len(now_days), "last_year_days": len(ly_days),
            "compared_days": len({off for _, off in common}),
            "comparable": comparable,
            "change_pct": _pct(now_same, ly_same) if comparable else None, "weeks": weeks}


async def utility_bill(session: AsyncSession, *, building_id: str, since: datetime,
                       until: datetime | None = None) -> dict[str, Any]:
    """Supply-meter consumption over the window against the same weeks last year, by fuel.

    The supply meters are counted once: a building's sub-meters sit beneath its incoming
    supply, so adding them counts the same kWh twice (Bishopsgate: 4.09 GWh against a supply
    of 1.51). A fuel with no supply meter falls back to its sub-meters, and says so. Last year
    is compared only when it is on record — twelve hours of it is not the same period.
    """
    until = until or datetime.now(timezone.utc)
    ly_start = a_year_before(since)
    ly_end = ly_start + (until - since)
    n_weeks = max(1, math.ceil((until - since).days / 7))
    window = {"since": iso(since), "until": iso(until), "last_year_since": iso(ly_start),
              "last_year_until": iso(ly_end), "weeks": n_weeks}
    rows = await read_rows(session, _BILL_DAYS,
                           {"bid": building_id, "since": since, "until": until,
                            "ly_start": ly_start, "ly_end": ly_end}, "billing")
    if rows is None:
        return {**_unreadable("metered consumption"), "window": window, "basis": BILL_BASIS}
    first = await read_rows(session, _BILL_FIRST, {"bid": building_id}, "billing_first") or []
    first_at = {r["meter_id"]: r.get("first_at") for r in first}

    meters: dict[str, dict[str, Any]] = {}
    for r in rows:
        meters.setdefault(r["meter_id"], {"meter_ref": r.get("meter_ref"), "fuel": r.get("fuel"),
                                          "sub": bool(r.get("sub")), "tariff": num(r.get("tariff"))})
    fuels: dict[str, Any] = {}
    used_rows: list[dict] = []
    used_meters: list[str] = []
    for fuel in sorted({m["fuel"] for m in meters.values()}):
        # A supply meter that read zero this window is still the supply — a boiler's gas in
        # summer reads 0, and falling back to its sub-meters hid a real drop as "flat".
        supply_all = [mid for mid, m in meters.items() if m["fuel"] == fuel and not m["sub"]]
        supply = [mid for mid in supply_all
                  if any(r["meter_id"] == mid and r["period"] == "now" and num(r["kwh"]) is not None
                         for r in rows)]
        basis = "supply"
        chosen = supply
        if not chosen:
            chosen = [mid for mid, m in meters.items() if m["fuel"] == fuel and m["sub"]]
            basis = ("sub-meters (the supply meter has no reading this window)" if supply_all
                     else "sub-meters (no supply meter)")
            if not chosen:
                chosen, basis = supply_all, "supply"
        fr = [r for r in rows if r["meter_id"] in chosen]
        if not fr:
            continue
        t = _period_totals(fr, since.date(), ly_start.date(), n_weeks)
        tariffs = [meters[mid]["tariff"] for mid in chosen]
        cost = None
        if tariffs and all(x is not None for x in tariffs):
            def spend(period: str) -> float:
                return round(sum((num(r["kwh"]) or 0) * meters[r["meter_id"]]["tariff"]
                                 for r in fr if r["period"] == period), 2)
            cost = {"now": spend("now"), "last_year": spend("last_year"),
                    "basis": "at the meter's tariff"}
        fuels[fuel] = {**t, "meters_basis": basis, "cost_gbp": cost}
        used_rows.extend(fr)
        used_meters.extend(chosen)

    if not used_rows or not any(r["period"] == "now" and num(r["kwh"]) for r in used_rows):
        return {"status": "not_found", "window": window, "basis": BILL_BASIS, "fuels": fuels,
                "detail": ("no meter on this building recorded consumption in the last "
                           f"{n_weeks} weeks")}
    total = _period_totals(used_rows, since.date(), ly_start.date(), n_weeks)
    firsts = [first_at[m] for m in used_meters if first_at.get(m) is not None]
    first_reading = str(iso(min(firsts, key=lambda x: str(iso(x))))) if firsts else None
    detail = BILL_BASIS
    if not total["comparable"]:
        detail = (f"{BILL_BASIS}. The metered record does not cover the same weeks last year: "
                  f"{total['last_year_days']} of {total['now_days']} days are on record"
                  + (f" — metered history starts {first_reading[:10]}" if first_reading else ""))
    return {
        "status": "found", "window": window, "basis": BILL_BASIS, "fuels": fuels,
        "total": {k: v for k, v in total.items() if k != "weeks"},
        "meters": [{"meter_ref": meters[m]["meter_ref"], "meter_type": meters[m]["fuel"],
                    "is_sub_meter": meters[m]["sub"],
                    "first_reading_at": iso(first_at.get(m))} for m in sorted(set(used_meters))],
        "first_reading_at": first_reading, "detail": detail,
    }


# ── weather (degree days) ────────────────────────────────────────────────────────────

_LOCATION = """
    SELECT b.building_id::text AS building_id, b.name,
           coalesce(to_jsonb(s)->>'postcode', to_jsonb(b)->>'postcode') AS postcode,
           coalesce(to_jsonb(s)->>'city', to_jsonb(b)->>'city') AS city,
           coalesce(to_jsonb(s)->>'country_code', to_jsonb(b)->>'country_code',
                    to_jsonb(b)->'raw_metadata'->>'country_code') AS country_code,
           coalesce(to_jsonb(b)->>'latitude', to_jsonb(s)->>'latitude') AS latitude,
           coalesce(to_jsonb(b)->>'longitude', to_jsonb(s)->>'longitude') AS longitude
      FROM plenum_cafm.buildings b
      LEFT JOIN plenum_cafm.sites s
             ON s.id::text = b.site_id::text OR s.site_id::text = b.site_id::text
     WHERE b.building_id::text = :bid
     LIMIT 1"""

_TABLE = """
    SELECT month, hdd, cdd, base_temp_c, station, source
      FROM plenum_cafm.weather_degree_days
     WHERE building_id::text = :bid
       AND month >= CAST(:ly_start AS date) AND month <= CAST(:end AS date)
     ORDER BY month"""


def _months_between(start: date, end: date) -> list[date]:
    out, m = [], start.replace(day=1)
    while m <= end:
        out.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def _dd(temps: list[tuple[date, float]]) -> tuple[float, float]:
    hdd = sum(max(0.0, BASE_TEMP_C - t) for _, t in temps)
    cdd = sum(max(0.0, t - BASE_TEMP_C) for _, t in temps)
    return round(hdd, 1), round(cdd, 1)


async def _locate(loc: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Coordinates for the building: recorded ones, else the postcode, else the city."""
    lat, lon = num(loc.get("latitude")), num(loc.get("longitude"))
    if lat is not None and lon is not None:
        return {"query": None, "name": loc.get("name"), "latitude": lat, "longitude": lon,
                "resolved_from": "recorded coordinates"}, []
    country = open_meteo.iso_country(loc.get("country_code"))
    tried = []
    for field in ("postcode", "city"):
        q = (loc.get(field) or "").strip()
        if not q:
            continue
        tried.append(q)
        hit = await open_meteo.geocode(q, country)
        if hit:
            return {"query": q, "name": hit["name"], "latitude": hit["latitude"],
                    "longitude": hit["longitude"], "resolved_from": field}, tried
    return None, tried


async def degree_days(session: AsyncSession, *, building_id: str, start: date, end: date,
                      today: date | None = None, monthly: bool = False) -> dict[str, Any]:
    """Heating and cooling degree days for the building, against the same dates last year.

    The platform's own weather_degree_days record is used when it covers the months asked;
    otherwise Open-Meteo's archive, computed at 15.5 °C and stored nowhere. The window ends at
    the last day the archive holds and never later than yesterday — today is partial — and
    last year is clipped to the same dates, so a missing week is not read as a mild one.
    """
    today = today or datetime.now(timezone.utc).date()
    end = min(end, today - timedelta(days=1))
    ly_start = _day_before_year(start)
    base = {"base_temp_c": BASE_TEMP_C, "window": {"start": start.isoformat(), "end": end.isoformat()}}

    # Months are stored on the 1st, so last year's first month is asked from its 1st: a
    # mid-month bound dropped it, and the platform's own record could never be comparable.
    table = await read_rows(session, _TABLE,
                            {"bid": building_id, "ly_start": ly_start.replace(day=1), "end": end},
                            "weather_table")
    if table is None:
        return {**_unreadable("degree-day record"), **base}
    wanted = {m for m in _months_between(start, end)}
    have = {_day(r["month"]).replace(day=1): r for r in table if _day(r.get("month"))}
    if table and wanted <= set(have):
        return _from_table(have, start, end, monthly, base)

    loc_rows = await read_rows(session, _LOCATION, {"bid": building_id}, "building_location")
    if loc_rows is None:
        return {**_unreadable("building location"), **base}
    if not loc_rows:
        return {"status": "not_found", **base, "detail": "the building is not on record"}
    loc = loc_rows[0]
    if not any((loc.get(k) or "").strip() if isinstance(loc.get(k), str) else loc.get(k)
               for k in ("postcode", "city", "latitude")):
        return {"status": "not_found", **base,
                "detail": ("no location on record for this building — its site carries no "
                           "postcode or city, so its weather cannot be looked up")}
    # Nothing more is read from the database, so the transaction ends before the wait
    # (db.py; reports/cards.py, 17 Sep 2026): an await inside it would hold its locks, and its
    # pooled connection, for as long as the weather service takes.
    await _release(session)

    async def fetch() -> tuple[dict[str, Any] | None, list[str], list]:
        where, tried = await _locate(loc)
        if where is None:
            return None, tried, []
        return where, tried, await open_meteo.daily_mean_temperature(
            where["latitude"], where["longitude"], ly_start, end)

    try:
        where, tried, temps = await asyncio.wait_for(fetch(), timeout=WEATHER_BUDGET_S)
    except asyncio.TimeoutError:
        return {"status": "unreadable", **base, "source": "open-meteo",
                "detail": f"the weather service did not answer within {WEATHER_BUDGET_S:g} s"}
    except open_meteo.WeatherUnavailable as exc:
        return {"status": "unreadable", **base, "source": "open-meteo",
                "detail": f"the weather archive could not be read: {exc}"}
    if where is None:
        return {"status": "not_found", **base, "source": "open-meteo",
                "detail": ("the building's location could not be found by the geocoder "
                           f"(tried {', '.join(tried)})")}

    have_t = {d: t for d, t in temps if t is not None}
    this_year = [d for d in sorted(have_t) if start <= d <= end]
    if not this_year:
        return {"status": "not_found", **base, "source": "open-meteo", "location": where,
                "detail": "the weather archive holds no day of this period yet"}
    end = this_year[-1]
    now = [(d, have_t[d]) for d in this_year]
    last = [(_day_before_year(d), have_t[_day_before_year(d)]) for d in this_year
            if _day_before_year(d) in have_t]
    hdd, cdd = _dd(now)
    ly_hdd, ly_cdd = _dd(last)
    comparable = len(last) >= COVERAGE_MIN * len(now)
    out = {
        "status": "found", "source": "open-meteo", "location": where,
        "base_temp_c": BASE_TEMP_C,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": len(now)},
        "total": {"hdd": hdd, "cdd": cdd, "last_year_hdd": ly_hdd, "last_year_cdd": ly_cdd,
                  "days": len(now), "last_year_days": len(last), "comparable": comparable,
                  "hdd_change_pct": _pct(hdd, ly_hdd) if comparable else None,
                  "cdd_change_pct": _pct(cdd, ly_cdd) if comparable else None},
        "detail": (f"daily mean temperature from Open-Meteo's ERA5 archive for {where['name']}, "
                   f"degree days at {BASE_TEMP_C} °C, to {end.isoformat()} — the archive's last "
                   "full day; stored nowhere"),
    }
    if monthly:
        months = []
        for m in _months_between(start, end):
            nm = [(d, t) for d, t in now if d.replace(day=1) == m]
            lm = [(d, t) for d, t in last if (d.replace(day=1) == _day_before_year(m))]
            h, c = _dd(nm)
            lh, lc = _dd(lm)
            months.append({"month": m.isoformat()[:7], "hdd": h, "cdd": c, "days": len(nm),
                           "last_year_hdd": lh, "last_year_cdd": lc, "last_year_days": len(lm)})
        out["months"] = months
    return out


def _from_table(have: dict[date, dict], start: date, end: date, monthly: bool,
                base: dict[str, Any]) -> dict[str, Any]:
    months = []
    for m in _months_between(start, end):
        r, ly = have.get(m, {}), have.get(_day_before_year(m), {})
        months.append({"month": m.isoformat()[:7], "hdd": num(r.get("hdd")), "cdd": num(r.get("cdd")),
                       "last_year_hdd": num(ly.get("hdd")) if ly else None,
                       "last_year_cdd": num(ly.get("cdd")) if ly else None})
    comparable = all(x["last_year_hdd"] is not None for x in months)
    hdd = round(sum(x["hdd"] or 0 for x in months), 1)
    cdd = round(sum(x["cdd"] or 0 for x in months), 1)
    ly_hdd = round(sum(x["last_year_hdd"] or 0 for x in months), 1) if comparable else None
    ly_cdd = round(sum(x["last_year_cdd"] or 0 for x in months), 1) if comparable else None
    first = next(iter(have.values()))
    out = {"status": "found", "source": "weather_degree_days",
           "station": first.get("station"), "provider": first.get("source"),
           "base_temp_c": num(first.get("base_temp_c")) or BASE_TEMP_C, "window": base["window"],
           "total": {"hdd": hdd, "cdd": cdd, "last_year_hdd": ly_hdd, "last_year_cdd": ly_cdd,
                     "comparable": comparable,
                     "hdd_change_pct": _pct(hdd, ly_hdd) if comparable else None,
                     "cdd_change_pct": _pct(cdd, ly_cdd) if comparable else None},
           "detail": "the platform's own degree-day record (weather_degree_days), by month"}
    if monthly:
        out["months"] = months
    return out
