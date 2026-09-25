"""Seed the energy and ratings inputs every named building needs for the Energy page to
show a figure instead of a dash — on a dataset the owner has confirmed is synthetic.

The tiles read real records: an EUI needs a meter with twelve months of readings and a
floor area; the UK MEES tiles need an EPC with a band; the US tiles need LL84 filings and
enough consumption for LL97 / Energy Star; the Singapore tiles need a BCA submission and a
Green Mark award; the UAE tiles need two buildings with an EUI and a chiller with a design
figure and readings. Where a named building lacks one of those, this writes it — stable
(md5 of the building id decides every choice, so a re-run adds nothing), labelled
(``source = 'simulator'``, ``raw_metadata.synthetic = true``, certificate numbers
``EPC-SYN-…``), and only where the record is missing. Generated demo buildings (MixedUse
017, Tower 123, OfficeCampus 003 …) are left alone.

    python db/tools/seed_energy_demo.py --db hoistra_test                # dry run
    python db/tools/seed_energy_demo.py --db plenum_agent --apply --validate
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", ".env")
NS = uuid.UUID("3e6d1a7f-9b2c-5d4e-8f01-e9a7c5b3d201")
GENERIC_NAME = re.compile(r"^(MixedUse|Tower|OfficeCampus|Retail|Warehouse|Hospital|Residential|[A-Za-z]+)\s+\d{3}$")
DAYS_OF_READINGS = 395
#: kWh/m²/yr the electricity feed is scaled to, by use — TM46 electricity figures.
ELEC_EUI_BY_USE = {"office": 95.0, "commercial": 95.0, "retail": 165.0, "residential": 60.0, "mixed": 110.0,
                   "hospital": 90.0, "warehouse": 35.0, "campus": 95.0, "education": 60.0}
GAS_EUI_BY_USE = {"office": 120.0, "commercial": 120.0, "retail": 0.0, "residential": 100.0, "mixed": 110.0,
                  "hospital": 420.0, "warehouse": 160.0, "campus": 120.0, "education": 150.0}
TARIFF = {"UK": 0.284, "US": 0.22, "AE": 0.445, "SG": 0.30}
#: Named production buildings that carry no country at all. Synthetic assignment so the US
#: and Singapore markets have something to score; recorded on the row as such.
COUNTRY_FOR_NAMELESS = {"Dock Logistics Park": "US", "Riverside Campus": "US", "Tech Park": "SG"}
EPC_BANDS = ["B", "C", "C", "D", "D", "E", "F", "G"]
EPC_SCORE = {"A": 15, "B": 40, "C": 62, "D": 82, "E": 105, "F": 130, "G": 160}


def dsn() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def h(key: str, n: int) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16) % max(1, n)


def hf(key: str) -> float:
    """A stable float in [0, 1)."""
    return h(key, 10_000) / 10_000.0


def use_for(name: str, use_type: str | None, primary_use: str | None) -> str:
    if use_type:
        return use_type
    if primary_use:
        return primary_use
    n = name.lower()
    if "logistic" in n or "dock" in n:
        return "Warehouse"
    if "hall" in n or "retail" in n or "house" in n:
        return "Commercial"
    if "campus" in n or "park" in n or "hub" in n:
        return "Office"
    return "Office"


def eui_key(use: str) -> str:
    u = use.lower()
    for k in ELEC_EUI_BY_USE:
        if k in u:
            return k
    return "office"


class Seeder:
    def __init__(self, conn: asyncpg.Connection, apply: bool, reseed: bool = False) -> None:
        self.c, self.apply, self.reseed = conn, apply, reseed
        self.counts: dict[str, int] = {}
        self.today = date.today()

    def note(self, what: str, n: int = 1) -> None:
        self.counts[what] = self.counts.get(what, 0) + n

    async def targets(self) -> list[dict]:
        rows = await self.c.fetch("""
            SELECT b.building_id, b.name, b.organization_id, b.gross_area_sqft, b.primary_use::text AS primary_use,
                   b.raw_metadata, s.site_id AS skey, s.id AS site_uuid_key,
                   coalesce(s.country_code, b.raw_metadata->>'country_code') AS cc, s.use_type, s.gfa_sqm,
                   s.benchmark_kwh_per_m2 AS recorded_benchmark
              FROM plenum_cafm.buildings b
              LEFT JOIN plenum_cafm.sites s ON s.id::text = b.site_id::text OR s.site_id::text = b.site_id::text
             ORDER BY b.name""")
        out, seen = [], set()
        for r in rows:
            d = dict(r)
            if GENERIC_NAME.match(d["name"] or "") or d["name"] in seen:
                continue
            if d["skey"] is None and d["site_uuid_key"] is None:
                continue  # no site row to hold the survey
            seen.add(d["name"])
            out.append(d)
        return out

    async def survey(self, b: dict) -> None:
        """Country, use and floor area on the site row — the facts the EUI and the rule need."""
        sets, params = [], {}
        cc = b["cc"] or COUNTRY_FOR_NAMELESS.get(b["name"]) or ("US" if h(str(b["building_id"]), 2) else "SG")
        if not b["cc"]:
            sets.append("country_code = $%d"); params["cc"] = cc
            self.note("site.country_code set")
        b["cc"] = cc
        use = use_for(b["name"], b["use_type"], b["primary_use"])
        if not b["use_type"]:
            sets.append("use_type = $%d"); params["use"] = use
            self.note("site.use_type set")
        b["use"] = use
        gfa = None
        if b["gfa_sqm"]:
            try:
                gfa = float(b["gfa_sqm"])
            except (TypeError, ValueError):
                gfa = None
        if gfa is None and b["gross_area_sqft"]:
            gfa = round(float(b["gross_area_sqft"]) / 10.7639, 0)
        if gfa is None:
            gfa = float(4000 + h(str(b["building_id"]) + "gfa", 36_000))
            sets.append("gfa_sqm = $%d"); params["gfa"] = str(int(gfa))
            self.note("site.gfa_sqm set")
        elif not b["gfa_sqm"]:
            sets.append("gfa_sqm = $%d"); params["gfa"] = str(int(gfa))
            self.note("site.gfa_sqm set")
        b["gfa"] = gfa
        if sets and self.apply:
            vals = list(params.values())
            sql = ("UPDATE plenum_cafm.sites SET " + ", ".join(s % (i + 1) for i, s in enumerate(sets))
                   + f" WHERE site_id = ${len(vals) + 1} OR id::text = ${len(vals) + 2}")
            await self.c.execute(sql, *vals, b["skey"], str(b["site_uuid_key"]) if b["site_uuid_key"] else None)
        # The building row says the country too, and that this row was seeded.
        if self.apply:
            await self.c.execute("""
                UPDATE plenum_cafm.buildings SET raw_metadata = coalesce(raw_metadata, '{}'::jsonb)
                    || jsonb_build_object('country_code', $2::text, 'synthetic_energy_seed', $3::text)
                 WHERE building_id = $1 AND (raw_metadata->>'synthetic_energy_seed') IS NULL""",
                b["building_id"], cc, self.today.isoformat())

    async def meters(self, b: dict) -> list[dict]:
        fuels = ["electricity"] + (["gas"] if b["cc"] in ("UK", "GB") and GAS_EUI_BY_USE.get(eui_key(b["use"]), 0) > 0 else [])
        b["has_gas_meter"] = "gas" in fuels
        out = []
        for fuel in fuels:
            existing = await self.c.fetchrow(
                "SELECT id, (SELECT count(*) FROM plenum_cafm.meter_readings r WHERE r.meter_id = m.id) AS readings,"
                " coalesce(raw_metadata->>'synthetic','false') = 'true' AS synthetic"
                " FROM plenum_cafm.energy_meters m WHERE m.building_id = $1 AND m.active AND m.meter_type = $2 ORDER BY created_at LIMIT 1",
                b["building_id"], fuel)
            if existing:
                out.append({"id": existing["id"], "fuel": fuel, "readings": existing["readings"], "synthetic": existing["synthetic"]})
                continue
            mid = uuid.uuid5(NS, f"meter:{b['building_id']}:{fuel}")
            self.note(f"meter.{fuel} created")
            if self.apply:
                ident = f"SYN-{str(mid)[:8].upper()}"
                await self.c.execute("""
                    INSERT INTO plenum_cafm.energy_meters
                        (id, organization_id, building_id, meter_type, mpan, mprn, tariff_gbp_per_kwh, carbon_kg_per_kwh,
                         is_sub_meter, active, raw_metadata, created_at, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 0.207, false, true, $8::jsonb, now(), now())
                    ON CONFLICT (id) DO NOTHING""",
                    mid, b["organization_id"], b["building_id"], fuel,
                    ident if fuel == "electricity" else None, ident if fuel == "gas" else None,
                    TARIFF.get(b["cc"], 0.28),
                    json.dumps({"simulate": True, "synthetic": True,
                                "sim_note": f"synthetic demo feed seeded {self.today.isoformat()} — every reading is generated"}))
            out.append({"id": mid, "fuel": fuel, "readings": 0, "synthetic": True})
        return out

    async def readings(self, b: dict, m: dict) -> None:
        if m["readings"] and self.reseed and m.get("synthetic"):
            self.note(f"readings.{m['fuel']} regenerated (old rows dropped)")
            if self.apply:
                await self.c.execute("DELETE FROM plenum_cafm.meter_readings WHERE meter_id = $1 AND source = 'simulator'", m["id"])
                await self.c.execute("DELETE FROM plenum_cafm.eui_snapshots WHERE building_id = $1", b["building_id"])
            m["readings"] = 0
        if m["readings"]:
            return
        key = eui_key(b["use"])
        elec, gas = ELEC_EUI_BY_USE[key], GAS_EUI_BY_USE[key]
        has_gas = b.get("has_gas_meter", False)
        recorded = float(b["recorded_benchmark"]) if b.get("recorded_benchmark") is not None else None
        # The feed is scaled to the figure this building is read against: the benchmark
        # recorded on its row (whole-building) when there is one, else TM46 for its use —
        # split between fuels where a gas meter exists, all on electricity where it does not.
        if m["fuel"] == "gas":
            share = gas / (elec + gas) if (elec + gas) else 0.5
            base = (recorded * share) if recorded else gas
        elif has_gas:
            share = elec / (elec + gas) if (elec + gas) else 0.5
            base = (recorded * share) if recorded else elec
        else:
            base = recorded if recorded else (elec + gas)
        # 0.80 … 1.35 × that figure, by building: some within, some over, the same every run.
        factor = 0.80 + 0.55 * hf(f"{b['building_id']}:{m['fuel']}:factor")
        annual_kwh = base * factor * b["gfa"]
        hourly = annual_kwh / 8760.0
        # Gas is a heating load: winter-heavy for the UK; electricity: office-hours shape with
        # a cooling summer for the Gulf and Singapore.
        cooling = b["cc"] in ("AE", "SG", "US")
        self.note(f"readings.{m['fuel']} hourly rows", DAYS_OF_READINGS * 24)
        if not self.apply:
            return
        await self.c.execute(f"""
            INSERT INTO plenum_cafm.meter_readings
                (id, organization_id, meter_id, reading_at, period_minutes, consumption_kwh, source, quality_flag, created_at)
            SELECT gen_random_uuid(), CAST($2 AS uuid), CAST($1 AS uuid), ts, 60,
                   round(($3::numeric
                     * CASE WHEN CAST($4 AS text) = 'gas'
                            THEN (0.35 + 1.30 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                            ELSE (CASE WHEN extract(isodow FROM ts) >= 6 THEN 0.45 ELSE 1.0 END)
                                 * (CASE WHEN extract(hour FROM ts) BETWEEN 7 AND 18 THEN 1.45 ELSE 0.55 END)
                                 * (CASE WHEN CAST($5 AS boolean) THEN (0.85 + 0.30 * (1 - cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                                         ELSE (0.92 + 0.16 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2) END)
                       END
                     * (0.94 + 0.12 * (('x' || substr(md5(CAST($1 AS text) || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)
                   )::numeric, 3),
                   'simulator', NULL, now()
              FROM generate_series(
                     date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => {DAYS_OF_READINGS}),
                     date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '1 hour', interval '1 hour') AS ts
            """, str(m["id"]), str(b["organization_id"]) if b["organization_id"] else None, hourly, m["fuel"], cooling)

    async def epc(self, b: dict) -> None:
        if b["cc"] not in ("UK", "GB"):
            return
        n = await self.c.fetchval(
            "SELECT count(*) FROM plenum_cafm.compliance_certificates WHERE building_id = $1 AND certificate_type_code IN ('EPC','DEC','EPC_NON_DOMESTIC','EPC_COMMERCIAL')",
            b["building_id"])
        if n:
            return
        band = EPC_BANDS[h(f"{b['building_id']}:epc", len(EPC_BANDS))]
        issued = date(2017 + h(f"{b['building_id']}:epcy", 9), 1 + h(f"{b['building_id']}:epcm", 12), 1 + h(f"{b['building_id']}:epcd", 28))
        expiry = issued.replace(year=issued.year + 10)
        # One in four expires inside the year, so the "expiring" count is not always zero.
        if h(f"{b['building_id']}:epcsoon", 4) == 0:
            expiry = self.today + timedelta(days=60 + h(f"{b['building_id']}:epcdays", 300))
            issued = expiry.replace(year=expiry.year - 10)
        cid = uuid.uuid5(NS, f"epc:{b['building_id']}")
        self.note(f"epc band {band}")
        if not self.apply:
            return
        await self.c.execute("""
            INSERT INTO plenum_cafm.compliance_certificates
                (id, org_id, organization_id, certificate_ref, cert_type, certificate_type_code, certificate_number, issuer,
                 issue_date, expiry_date, next_due_date, inspection_frequency_months, result, remedial_status, cert_scope,
                 country_code, status, insurance_risk_flag, raw_metadata, building_id, building_name, site_ref,
                 energy_rating, energy_score, created_at, updated_at)
            VALUES ($1, $2, $2, $3, 'EPC', 'EPC', $3, 'Accredited Energy Assessor (synthetic demo)', $4, $5, $5, 120,
                    $6, 'Closed', 'Building', 'UK', $7, false, $8::jsonb, $9, $10, $11, $6, $12, now(), now())
            ON CONFLICT (id) DO NOTHING""",
            cid, b["organization_id"], f"EPC-SYN-{str(cid)[:8].upper()}", issued, expiry, band,
            "Current" if expiry >= self.today else "Expired",
            json.dumps({"synthetic": True, "site_label": b["name"], "seeded": self.today.isoformat()}),
            b["building_id"], b["name"], b["skey"], EPC_SCORE[band])

    async def filings(self, b: dict) -> None:
        if b["cc"] not in ("US", "SG"):
            return
        want = []
        if b["cc"] == "US":
            # Filed for most; every third building left unfiled so the tile can say Overdue.
            if h(f"{b['building_id']}:ll84", 3) != 0:
                want.append(("LL84", 2025, "filed", date(2026, 4, 20 + h(f"{b['building_id']}:d", 9)), date(2026, 5, 1), None))
        else:
            if h(f"{b['building_id']}:bca", 3) != 0:
                want.append(("BCA_BENCHMARKING", 2025, "filed", date(2026, 9, 1 + h(f"{b['building_id']}:d", 12)), date(2026, 9, 30), None))
            level = ("Certified", "Gold", "GoldPlus", "Platinum")[h(f"{b['building_id']}:gm", 4)]
            want.append(("GREEN_MARK", 2024, "certified", date(2024, 3 + h(f"{b['building_id']}:gmm", 9), 1), None, level))
        for scheme, year, status, filed_at, due, level in want:
            exists = await self.c.fetchval(
                "SELECT count(*) FROM plenum_cafm.regulatory_filings WHERE building_id = $1 AND scheme = $2 AND period_year = $3",
                b["building_id"], scheme, year)
            if exists:
                continue
            self.note(f"filing {scheme} {status}")
            if self.apply:
                await self.c.execute("""
                    INSERT INTO plenum_cafm.regulatory_filings
                        (id, organization_id, building_id, scheme, period_year, status, filed_at, due_date, reference,
                         certification_level, detail_json)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, '{"synthetic": true}'::jsonb) ON CONFLICT DO NOTHING""",
                    uuid.uuid5(NS, f"filing:{b['building_id']}:{scheme}:{year}"), b["organization_id"], b["building_id"],
                    scheme, year, status, filed_at, due, f"SYN-{scheme}-{year}-{str(b['building_id'])[:6]}", level)

    async def chiller(self, b: dict) -> None:
        if b["cc"] != "AE":
            return
        if await self.c.fetchval("SELECT count(*) FROM plenum_cafm.chiller_design_specs WHERE building_id = $1", b["building_id"]):
            return
        # A uuid-keyed asset on the building if it has one; otherwise a stable synthetic id.
        asset = await self.c.fetchval(
            "SELECT id::text FROM plenum_cafm.assets WHERE building_id = $1 AND id::text ~ '^[0-9a-f]{8}-'"
            " ORDER BY (coalesce(asset_code,'') || ' ' || coalesce(asset_name,'')) ILIKE '%chill%' DESC, id LIMIT 1",
            b["building_id"])
        asset_id = uuid.UUID(asset) if asset else uuid.uuid5(NS, f"chiller-asset:{b['building_id']}")
        design = 0.62 + 0.10 * hf(f"{b['building_id']}:design")
        capacity = 300 + h(f"{b['building_id']}:cap", 500)
        drift = 1.0 + 0.28 * hf(f"{b['building_id']}:drift")   # up to +28 % — some plants breach 15 %
        self.note(f"chiller spec (drift {drift:.2f}) + 30 days hourly readings")
        if not self.apply:
            return
        await self.c.execute("""
            INSERT INTO plenum_cafm.chiller_design_specs
                (asset_id, building_id, organization_id, design_kw_per_rt, design_capacity_rt, design_ambient_c, source, notes, updated_at)
            VALUES ($1, $2, $3, $4, $5, 35, 'synthetic', 'synthetic demo design figures', now())
            ON CONFLICT DO NOTHING""",
            asset_id, b["building_id"], b["organization_id"], round(design, 4), capacity)
        await self.c.execute("""
            INSERT INTO plenum_cafm.chiller_performance_readings
                (id, organization_id, asset_id, building_id, reading_at, kw_input, cooling_load_rt, ambient_c, source, created_at)
            SELECT gen_random_uuid(), CAST($1 AS uuid), CAST($2 AS uuid), CAST($3 AS uuid), ts,
                   round((CAST($5 AS float8) * CAST($6 AS float8) * CAST($4 AS float8) * load)::numeric, 2),
                   round((CAST($5 AS float8) * load)::numeric, 2),
                   round((30 + 9 * (CASE WHEN extract(hour FROM ts) BETWEEN 11 AND 17 THEN 1 ELSE 0.4 END))::numeric, 1),
                   'synthetic', now()
              FROM (SELECT ts, (0.45 + 0.50 * (CASE WHEN extract(hour FROM ts) BETWEEN 9 AND 18 THEN 1 ELSE 0.35 END)
                              * (0.9 + 0.2 * (('x' || substr(md5(CAST($2 AS text) || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)) AS load
                      FROM generate_series(date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '30 days',
                                           date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '1 hour', interval '1 hour') AS ts) g
            """, str(b["organization_id"]) if b["organization_id"] else None, str(asset_id), str(b["building_id"]),
                 drift, float(capacity), round(design, 4))

    async def run(self) -> list[dict]:
        targets = await self.targets()
        for b in targets:
            await self.survey(b)
            for m in await self.meters(b):
                await self.readings(b, m)
            await self.epc(b)
            await self.filings(b)
            await self.chiller(b)
        return targets


async def validate_and_scan(db: str) -> None:
    """Run the benchmark validation and the anomaly scan through the service's own engines."""
    raw = dsn()
    os.environ["DB_URL"] = re.sub(r"^postgresql://", "postgresql+asyncpg://", raw.split("?")[0]).rsplit("/", 1)[0] + f"/{db}"
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from src import db as _db  # noqa: E402
    from src.engines.energy import anomalies, benchmarks  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    from uuid import UUID  # noqa: E402
    async with _db.AsyncSessionLocal() as s:
        ids = [UUID(str(r)) for r in (await s.execute(text("SELECT building_id FROM plenum_cafm.buildings"))).scalars().all()]
        rep = await benchmarks.validate(s, building_ids=ids, organization_id=None, persist=True)
        print("  validation:", rep["summary"], "snapshots written:", rep["snapshots_written"])
        for cc, m in sorted(rep["markets"].items()):
            print(f"    {cc}: {m['validated']}/{m['buildings']} validated · derived {m['derived']} · tiles {m['tiles_ready']}")
    async with _db.AsyncSessionLocal() as s:
        try:
            res = await anomalies.scan_all_active_meters(s, limit=500)
            print("  anomaly scan:", {k: v for k, v in res.items() if k != "results"} if isinstance(res, dict) else res)
        except Exception as exc:  # noqa: BLE001
            print("  anomaly scan failed:", str(exc)[:200])
    await _db.engine.dispose()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--validate", action="store_true", help="after seeding, run the benchmark validation and anomaly scan")
    ap.add_argument("--reseed-readings", action="store_true",
                    help="drop this tool's own generated readings (and the snapshots derived from them) and generate them again")
    args = ap.parse_args()
    conn = await asyncpg.connect(dsn(), database=args.db, timeout=60)
    print(f"== {args.db} — {'APPLY' if args.apply else 'dry run'}")
    seeder = Seeder(conn, args.apply, reseed=args.reseed_readings)
    tx = conn.transaction()
    await tx.start()
    try:
        targets = await seeder.run()
        if args.apply:
            await tx.commit()
        else:
            await tx.rollback()
    except Exception:
        await tx.rollback()
        raise
    print(f"  {len(targets)} named buildings:", ", ".join(f"{t['name']} ({t['cc']}, {t['use']}, {int(t['gfa'])} m²)" for t in targets))
    for k, v in sorted(seeder.counts.items()):
        print(f"  {k}: {v:,}")
    if not args.apply:
        print("  dry run — nothing written; re-run with --apply")
    await conn.close()
    if args.apply and args.validate:
        await validate_and_scan(args.db)


if __name__ == "__main__":
    asyncio.run(main())
