"""The plant telemetry and weather a single end-to-end workbook carries, into the stores the
energy engines read.

None of these is a table the migration copies - each has an ingest that owns its shape - so the
migration sets the sheets aside and contract_performance.workbook_extras hands them here after
the write, with the contract terms and invoices:

  Chiller_Design_Specs  asset_code, design_kw_per_rt, design_capacity_rt, design_ambient_c,
                        design_chw_supply_c, source, notes      -> chiller.upsert_design_spec
  Chiller_Readings      asset_code, reading_at, kw_input, cooling_load_rt, ambient_c,
                        chw_supply_c, chw_return_c             -> chiller.ingest_readings
  Weather_Degree_Days   building_code, month, hdd, cdd, base_temp_c, station
                                                               -> ratings_position.ingest_degree_days
  BMS_Trends            building_code, zone, asset_code, recorded_at, heating_pct, cooling_pct,
                        zone_temp_c, setpoint_c                -> ratings_position.ingest_bms_trends

These are what the energy scan's chiller and simultaneous-heating-and-cooling rules and the
investigation's bms_trend and weather sources read; without them each reports "no readings".

Repeatable: design specs and degree days upsert, and the readings and BMS samples this source
wrote before over the same span are replaced rather than doubled.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from . import chiller as chiller_svc
from . import ratings_position as position_svc

log = get_logger(__name__)

#: The normalised sheet names this reads (workbook_extras._norm).
SHEETS = ("chillerdesignspecs", "chillerreadings", "weatherdegreedays", "bmstrends")
SOURCE = "workbook"


def _f(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _at(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _month(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date().replace(day=1)
    if isinstance(v, date):
        return v.replace(day=1)
    try:
        return date.fromisoformat(str(v).strip()[:10]).replace(day=1)
    except ValueError:
        try:
            return date.fromisoformat(str(v).strip()[:7] + "-01")
        except ValueError:
            return None


class _Resolver:
    """Codes to ids within one company. The buildings table is not the same shape on every
    database (id or building_id; building_code or code), so its columns are read, not assumed."""

    def __init__(self, session: AsyncSession, org: UUID) -> None:
        self.s, self.org = session, str(org)
        self._assets: dict[str, tuple[str, str | None] | None] = {}
        self._buildings: dict[str, str | None] = {}
        self._bcols: tuple[str, str | None] | None = None

    async def asset(self, code: Any) -> tuple[str, str | None] | None:
        c = str(code or "").strip()
        if not c:
            return None
        if c not in self._assets:
            row = (await self.s.execute(text(
                """SELECT id::text, building_id::text FROM plenum_cafm.assets
                    WHERE organization_id::text = :o AND asset_code = :c LIMIT 1"""),
                {"o": self.org, "c": c})).first()
            self._assets[c] = (row[0], row[1]) if row else None
        return self._assets[c]

    async def building(self, code: Any) -> str | None:
        c = str(code or "").strip()
        if not c:
            return None
        if c not in self._buildings:
            if self._bcols is None:
                cols = {r[0] for r in (await self.s.execute(text(
                    """SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'plenum_cafm' AND table_name = 'buildings'"""))).all()}
                self._bcols = ("building_id" if "building_id" in cols else "id",
                               next((k for k in ("building_code", "code") if k in cols), None))
            idc, codec = self._bcols
            bid = None
            if codec:
                bid = (await self.s.execute(text(
                    f"""SELECT {idc}::text FROM plenum_cafm.buildings
                         WHERE organization_id::text = :o AND {codec} = :c LIMIT 1"""),
                    {"o": self.org, "c": c})).scalar()
            if bid is None:
                # No code column, or the building was created without one: the building its
                # assets were migrated into, by the code they carry as a prefix.
                bid = (await self.s.execute(text(
                    """SELECT building_id::text FROM plenum_cafm.assets
                        WHERE organization_id::text = :o AND asset_code LIKE :p AND building_id IS NOT NULL
                        GROUP BY 1 ORDER BY count(*) DESC LIMIT 1"""),
                    {"o": self.org, "p": c + "-%"})).scalar()
            self._buildings[c] = bid
        return self._buildings[c]


async def run(session: AsyncSession, sheets: dict[str, list[dict[str, Any]]],
              organization_id: UUID) -> dict[str, Any]:
    res = _Resolver(session, organization_id)
    out: dict[str, Any] = {"chiller_specs": 0, "chiller_readings": 0, "degree_days": 0, "bms_samples": 0,
                           "skipped": []}

    for r in sheets.get("chillerdesignspecs") or []:
        a = await res.asset(r.get("asset_code"))
        kwrt = _f(r.get("design_kw_per_rt"))
        if not a or not kwrt:
            out["skipped"].append({"asset_code": r.get("asset_code"),
                                   "reason": "asset not on record" if not a else "no design kW/RT"})
            continue
        await chiller_svc.upsert_design_spec(
            session, asset_id=UUID(a[0]), design_kw_per_rt=kwrt,
            building_id=UUID(a[1]) if a[1] else None, organization_id=organization_id,
            design_capacity_rt=_f(r.get("design_capacity_rt")), design_ambient_c=_f(r.get("design_ambient_c")),
            design_chw_supply_c=_f(r.get("design_chw_supply_c")),
            source=str(r.get("source") or SOURCE)[:80], notes=r.get("notes") or None)
        out["chiller_specs"] += 1

    by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sheets.get("chillerreadings") or []:
        at = _at(r.get("reading_at"))
        if at is not None and _f(r.get("kw_input")) is not None:
            by_asset[str(r.get("asset_code") or "").strip()].append({**r, "reading_at": at})
    for code, rows in by_asset.items():
        a = await res.asset(code)
        if not a:
            out["skipped"].append({"asset_code": code, "reason": "asset not on record"})
            continue
        lo, hi = min(x["reading_at"] for x in rows), max(x["reading_at"] for x in rows)
        await session.execute(text(
            """DELETE FROM plenum_cafm.chiller_performance_readings
                WHERE asset_id = CAST(:a AS uuid) AND source = :src AND reading_at BETWEEN :lo AND :hi"""),
            {"a": a[0], "src": SOURCE, "lo": lo, "hi": hi})
        r = await chiller_svc.ingest_readings(
            session, asset_id=UUID(a[0]), building_id=UUID(a[1]) if a[1] else None,
            organization_id=organization_id, source=SOURCE,
            readings=[{"reading_at": x["reading_at"], "kw_input": _f(x.get("kw_input")),
                       "cooling_load_rt": _f(x.get("cooling_load_rt")), "cooling_load_kw": _f(x.get("cooling_load_kw")),
                       "ambient_c": _f(x.get("ambient_c")), "chw_supply_c": _f(x.get("chw_supply_c")),
                       "chw_return_c": _f(x.get("chw_return_c"))} for x in rows])
        out["chiller_readings"] += int(r.get("stored") or 0)

    by_bld: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sheets.get("weatherdegreedays") or []:
        m = _month(r.get("month"))
        if m is not None:
            by_bld[str(r.get("building_code") or "").strip()].append({**r, "month": m})
    for code, rows in by_bld.items():
        bid = await res.building(code)
        if not bid:
            out["skipped"].append({"building_code": code, "reason": "building not on record"})
            continue
        r = await position_svc.ingest_degree_days(
            session, building_id=UUID(bid), organization_id=organization_id,
            months=[{"month": x["month"], "hdd": _f(x.get("hdd")), "cdd": _f(x.get("cdd"))} for x in rows],
            base_temp_c=_f(rows[0].get("base_temp_c")) or 15.5, station=rows[0].get("station") or None,
            source=SOURCE)
        out["degree_days"] += int(r.get("stored") or 0)

    by_bld = defaultdict(list)
    for r in sheets.get("bmstrends") or []:
        at = _at(r.get("recorded_at"))
        if at is not None and r.get("zone"):
            by_bld[str(r.get("building_code") or "").strip()].append({**r, "recorded_at": at})
    for code, rows in by_bld.items():
        bid = await res.building(code)
        if not bid:
            out["skipped"].append({"building_code": code, "reason": "building not on record"})
            continue
        lo, hi = min(x["recorded_at"] for x in rows), max(x["recorded_at"] for x in rows)
        await session.execute(text(
            """DELETE FROM plenum_cafm.bms_trends
                WHERE building_id = CAST(:b AS uuid) AND source = :src AND recorded_at BETWEEN :lo AND :hi"""),
            {"b": bid, "src": SOURCE, "lo": lo, "hi": hi})
        samples = []
        for x in rows:
            a = await res.asset(x.get("asset_code"))
            samples.append({"recorded_at": x["recorded_at"], "zone": str(x["zone"]), "asset_id": a[0] if a else None,
                            "heating_pct": _f(x.get("heating_pct")), "cooling_pct": _f(x.get("cooling_pct")),
                            "zone_temp_c": _f(x.get("zone_temp_c")), "setpoint_c": _f(x.get("setpoint_c"))})
        r = await position_svc.ingest_bms_trends(session, building_id=UUID(bid), organization_id=organization_id,
                                                 samples=samples, source=SOURCE)
        out["bms_samples"] += int(r.get("stored") or 0)

    log.info("workbook_telemetry.done", **{k: v for k, v in out.items() if k != "skipped"},
             skipped=len(out["skipped"]))
    return out
