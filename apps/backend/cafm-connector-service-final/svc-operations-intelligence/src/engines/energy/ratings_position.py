"""The ratings-and-duties tiles, per market, from real records.

The energy page shows three tiles per country and says what each is based on: a certificate
(actual the day it is on file), a filing (the same), or consumption (actual at twelve months,
projected from three, not shown below). Until now those tiles were constants in the frontend.
This assembles them from the engines:

    UK  MEES enforceable now · MEES proposed 2030 · EPCs on file        (epc_rating)
    US  LL97 limit · Energy Star score · LL84 benchmarking               (us_ratings, filings)
    SG  BCA energy submission · EUI vs BCA reference · Green Mark        (filings, eui)
    AE  Operational rating (none) · EUI vs rolling benchmark · Chiller kW/RT  (buildings, chiller)

Also here: the two small ingest paths for the inputs the weather and simultaneous-heating-
and-cooling rules need, and the one helper every route uses to turn a caller's scope into
the list of buildings it may see.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ..auth import access
from ..compliance import epc_rating, filings
from ...models.energy import BmsTrend, WeatherDegreeDays
from . import chiller as chiller_svc
from . import us_ratings

log = get_logger(__name__)

BCA_OFFICE_REFERENCE_KWH_M2 = 192.0   # BCA Building Energy Benchmarking Report, office median
AE_ROLLING_LABEL = "rolling portfolio benchmark"


async def building_ids_for(
    session: AsyncSession, scope: access.Scope, building_id: UUID | None
) -> list[UUID]:
    """The buildings a request may cover: one named building (checked), or every building
    the caller may see — their allocation for a user, the company's portfolio for an admin."""
    if building_id is not None:
        access.assert_building(scope, building_id, action="read")
        return [building_id]
    if scope.restricted:
        return list(scope.building_ids or ())
    where = "WHERE organization_id = CAST(:o AS uuid)" if scope.organization_id else ""
    rows = (await session.execute(text(f"SELECT building_id FROM plenum_cafm.buildings {where}"),
                                  {"o": str(scope.organization_id)} if scope.organization_id else {})).all()
    return [r[0] for r in rows]


async def _buildings_in_country(session: AsyncSession, building_ids: list[UUID], country_code: str) -> list[dict[str, Any]]:
    if not building_ids:
        return []
    rows = (await session.execute(text("""
        SELECT b.building_id::text AS building_id, b.name, b.gross_area_sqft,
               coalesce(s.country_code, b.raw_metadata->>'country_code') AS country_code,
               s.eui_kwh_per_m2, s.benchmark_kwh_per_m2, s.use_type, b.primary_use::text AS primary_use
          FROM plenum_cafm.buildings b
          LEFT JOIN plenum_cafm.sites s ON s.id = b.site_id OR s.site_id = b.site_id
         WHERE b.building_id = ANY(CAST(:ids AS uuid[]))
    """), {"ids": [str(b) for b in building_ids]})).mappings().all()
    return [dict(r) for r in rows if (r["country_code"] or "").upper() == country_code.upper()]


def _months_tile_ok(months: int) -> bool:
    return months >= us_ratings.MIN_MONTHS_TO_SHOW


async def position(
    session: AsyncSession, *, country_code: str, organization_id: UUID | None, building_ids: list[UUID]
) -> dict[str, Any]:
    cc = country_code.upper()
    blds = await _buildings_in_country(session, building_ids, cc)
    ids = [UUID(b["building_id"]) for b in blds]
    out: dict[str, Any] = {"ok": True, "country_code": cc, "buildings": len(ids), "tiles": [], "detail": {}}
    if cc == "UK":
        mees = await epc_rating.mees_summary(session, organization_id=organization_id, building_ids=ids)
        out["tiles"] = mees["tiles"]
        out["detail"]["mees"] = {k: v for k, v in mees.items() if k not in ("tiles",)}
        return out

    if cc == "US":
        latest = await us_ratings.latest_ratings(session, building_ids=ids)
        ll97 = [r for r in latest if r["scheme"] == "ll97" and _months_tile_ok(r["months_of_data"])]
        es = [r for r in latest if r["scheme"] == "energy_star" and _months_tile_ok(r["months_of_data"])]
        if ll97:
            over = [r for r in ll97 if r["status"] == "over"]
            months = min(r["months_of_data"] for r in ll97)
            worst = min(ll97, key=lambda r: (r["detail"].get("headroom_pct") or 0))
            period = worst["detail"].get("compliance_period", "2024-2029").replace("-", "–")
            out["tiles"].append({
                "l": f"LL97 {period[:4]}–{period[-2:]} limit", "v": "Over" if over else "Within",
                "s": (f"{len(over)} of {len(ll97)} over the occupancy-group cap" if over
                      else f"{worst['detail'].get('building_name') or 'portfolio'} · {len(ll97)} of {len(ll97)} under its "
                           f"occupancy-group cap · {worst['detail'].get('headroom_pct')}% headroom"),
                "tone": "risk" if over else "ok", "basis": "consumption", "months": months})
        else:
            out["tiles"].append({"l": "LL97 limit", "v": "—", "s": "no twelve-month position computed yet · POST /api/energy/ratings/compute",
                                 "tone": "dormant", "basis": "consumption", "months": 0})
        if es:
            best = max(es, key=lambda r: r["value"] or 0)
            score = int(best["value"] or 0)
            short = max(0, us_ratings.CERTIFICATION_SCORE - score)
            out["tiles"].append({
                "l": "Energy Star score", "v": str(score),
                "s": (f"certification at {us_ratings.CERTIFICATION_SCORE} · {short} points short" if short
                      else f"certifiable · at or above {us_ratings.CERTIFICATION_SCORE}") + " · estimate",
                "tone": "ok" if not short else ("warn" if short <= 10 else "risk"),
                "basis": "consumption", "months": best["months_of_data"]})
        else:
            out["tiles"].append({"l": "Energy Star score", "v": "—", "s": "no score estimated yet",
                                 "tone": "dormant", "basis": "consumption", "months": 0})
        fil = await filings.filing_positions(session, organization_id=organization_id, building_ids=ids, country_code="US")
        out["tiles"] += fil["tiles"]
        out["detail"] = {"ratings": latest, "filings": fil["schemes"]}
        return out

    if cc == "SG":
        fil = await filings.filing_positions(session, organization_id=organization_id, building_ids=ids, country_code="SG")
        tiles = {t["l"]: t for t in fil["tiles"]}
        if "BCA energy submission" in tiles:
            out["tiles"].append(tiles["BCA energy submission"])
        euis = [(b, float(b["eui_kwh_per_m2"])) for b in blds if b.get("eui_kwh_per_m2") is not None]
        if euis:
            b, eui = max(euis, key=lambda x: x[1])
            ref = float(b["benchmark_kwh_per_m2"] or BCA_OFFICE_REFERENCE_KWH_M2)
            dev = 100.0 * (eui - ref) / ref
            out["tiles"].append({"l": "EUI vs BCA reference", "v": f"{dev:+.0f}%",
                                 "s": f"{eui:.0f} against {ref:.0f} kWh/m²/yr", "tone": "ok" if dev <= 0 else ("warn" if dev <= 10 else "risk"),
                                 "basis": "consumption", "months": 12})
        else:
            out["tiles"].append({"l": "EUI vs BCA reference", "v": "—", "s": "no EUI on the building yet",
                                 "tone": "dormant", "basis": "consumption", "months": 0})
        if "Green Mark" in tiles:
            out["tiles"].append(tiles["Green Mark"])
        out["detail"] = {"filings": fil["schemes"]}
        return out

    if cc == "AE":
        out["tiles"].append({"l": "Operational rating", "v": "None",
                             "s": "Estidama and Al Sa'fat rate new build only", "tone": "dormant", "basis": "scheme"})
        euis = [(b, float(b["eui_kwh_per_m2"]), float(b["benchmark_kwh_per_m2"]))
                for b in blds if b.get("eui_kwh_per_m2") is not None and b.get("benchmark_kwh_per_m2")]
        if euis:
            b, eui, ref = max(euis, key=lambda x: x[1] / x[2])
            dev = 100.0 * (eui - ref) / ref
            out["tiles"].append({"l": "EUI vs rolling benchmark", "v": f"{dev:+.0f}%",
                                 "s": f"{eui:.0f} against {ref:.0f} kWh/m²/yr · {AE_ROLLING_LABEL}",
                                 "tone": "ok" if dev <= 0 else ("warn" if dev <= 10 else "risk"),
                                 "basis": "consumption", "months": 12})
        else:
            out["tiles"].append({"l": "EUI vs rolling benchmark", "v": "—",
                                 "s": "needs two comparable buildings with an EUI", "tone": "dormant",
                                 "basis": "consumption", "months": 0})
        plant = await chiller_svc.plant_position(session, building_ids=ids)
        if plant.get("tile"):
            out["tiles"].append(plant["tile"])
        out["detail"] = {"chillers": {k: v for k, v in plant.items() if k != "tile"}}
        return out

    out["tiles"].append({"l": "Operational rating", "v": "None", "s": f"no regulation pack for {cc}",
                         "tone": "dormant", "basis": "scheme"})
    return out


# ── inputs for two of the detectors ─────────────────────────────────────────────────

async def ingest_degree_days(
    session: AsyncSession, *, building_id: UUID, organization_id: UUID | None,
    months: list[dict[str, Any]], base_temp_c: float = 15.5, station: str | None = None,
    source: str = "manual",
) -> dict[str, Any]:
    stored = rejected = 0
    for m in months:
        try:
            raw = m["month"]
            d = date.fromisoformat(str(raw)[:10]).replace(day=1) if not isinstance(raw, date) else raw.replace(day=1)
            hdd, cdd = float(m.get("hdd") or 0), float(m.get("cdd") or 0)
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        await session.execute(text("""
            INSERT INTO plenum_cafm.weather_degree_days
                (id, organization_id, building_id, month, hdd, cdd, base_temp_c, station, source)
            VALUES (gen_random_uuid(), CAST(:o AS uuid), CAST(:b AS uuid), CAST(:m AS date), :h, :c, :t, :st, :src)
            ON CONFLICT (building_id, month) DO UPDATE
               SET hdd = EXCLUDED.hdd, cdd = EXCLUDED.cdd, base_temp_c = EXCLUDED.base_temp_c,
                   station = coalesce(EXCLUDED.station, plenum_cafm.weather_degree_days.station),
                   source = EXCLUDED.source
        """), {"o": str(organization_id) if organization_id else None, "b": str(building_id),
               "m": d.isoformat(), "h": hdd, "c": cdd, "t": base_temp_c, "st": station, "src": source})
        stored += 1
    await session.commit()
    return {"ok": True, "building_id": str(building_id), "stored": stored, "rejected": rejected}


async def ingest_bms_trends(
    session: AsyncSession, *, building_id: UUID, organization_id: UUID | None,
    samples: list[dict[str, Any]], source: str = "bms",
) -> dict[str, Any]:
    stored = rejected = 0
    for smp in samples:
        try:
            at = smp["recorded_at"]
            at = datetime.fromisoformat(at) if isinstance(at, str) else at
            at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
            zone = str(smp["zone"])
        except (KeyError, TypeError, ValueError):
            rejected += 1
            continue
        def _d(k: str) -> Decimal | None:
            v = smp.get(k)
            return Decimal(str(v)) if v is not None else None
        aid = smp.get("asset_id")
        session.add(BmsTrend(id=uuid4(), organization_id=organization_id, building_id=building_id,
                             asset_id=UUID(str(aid)) if aid else None, zone=zone, recorded_at=at,
                             heating_pct=_d("heating_pct"), cooling_pct=_d("cooling_pct"),
                             zone_temp_c=_d("zone_temp_c"), setpoint_c=_d("setpoint_c"), source=source))
        stored += 1
    await session.commit()
    return {"ok": True, "building_id": str(building_id), "stored": stored, "rejected": rejected}
