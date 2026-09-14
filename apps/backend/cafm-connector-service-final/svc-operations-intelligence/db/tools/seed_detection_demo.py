"""Give every named building the inputs the thirteen anomaly rules read — and, on a dataset
the owner has confirmed is synthetic, the faults that make them fire.

Every anomaly on the page was "gas meter · baseline drift" because the synthetic gas feed
carried a strong season (which the drift rule reads as a level change) and the electricity
feed carried nothing at all: a flat weekly shape with no weekend load, no night creep, no
early start, no spike, no gap, and no sub-meter, BMS trend, degree days or closed work order
for the rules that need them. Ten of the thirteen rules had nothing to look at.

This seeds, per named building, only where missing and always stable (md5 of the building
id decides), always labelled synthetic:

  main meter   half-hourly readings (the rules count half-hours), with two faults per
               building drawn from: weekend load, non-occupancy load, early plant start,
               overnight baseload creep, peak excursions, a data gap + flatline + estimated
               reads, peak-band load, a step drift, a post-works regression
  metadata     occupancy hours, tariff bands, off-peak rate and capacity_kw on the meter —
               the inputs schedule, TOU and peak rules read
  sub-meters   two per building on its assets (AHU / chiller plant), 60 days half-hourly;
               one carries a sustained asset spike on some buildings
  BMS trends   7 days × 15 min in three zones, one zone heating and cooling together for an
               hour a day, on buildings drawn for the "fight" fault
  degree days  13 months of HDD/CDD for the building's climate (weather residual)
  work order   one existing work order on the building closed 20 days ago (regression)

Then, with --validate: the anomaly scan over every active meter, the chiller scan, and the
detection-coverage report — which rules ran, fired or were skipped, per building.

    python db/tools/seed_detection_demo.py --db hoistra_test                # dry run
    python db/tools/seed_detection_demo.py --db plenum_agent --apply --validate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from seed_energy_demo import (  # noqa: E402 — shares the target rule and the stable hash
    ELEC_EUI_BY_USE, GAS_EUI_BY_USE, NS, Seeder, dsn, eui_key, h, hf,
)

sys.stdout.reconfigure(encoding="utf-8")

DAYS = 395
FAULTS = ["weekend", "nonocc", "schedule", "baseload", "peak", "dataq", "tou", "drift", "regress"]
#: Peak-band tariff premium and the off-peak rate as fractions of the flat tariff.
PEAK_PREMIUM, OFFPEAK = 1.5, 0.7
ZONES = ("L1 East", "L2 West", "Plant")


def faults_for(building_id: str) -> list[str]:
    """Two faults per building, never the same pair twice in a row of the hash."""
    a = h(f"{building_id}:f1", len(FAULTS))
    b = (a + 1 + h(f"{building_id}:f2", len(FAULTS) - 1)) % len(FAULTS)
    return [FAULTS[a], FAULTS[b]]


#: Faults that hide another fault on the same feed, so the top-up never pairs them.
#: Baseload creep is the one that matters: the detector only reports a rising overnight
#: floor while weekday daytime stays within 5%, and both the drift step and the
#: post-works dip move daytime well past that — inject them together and the rule can
#: never fire, which reads as a dead detector rather than a masked one.
CONFLICTS: dict[str, set[str]] = {"baseload": {"drift", "regress"}}


def assign_faults(building_ids: list[str]) -> dict[str, list[str]]:
    """The hash pair per building, then a top-up so every fault lands somewhere it can fire.

    Two faults drawn from nine leaves gaps: on a nine-building estate the hash gave baseload
    creep to nobody, so that rule had nothing to find and the report read as a hole in the
    detector rather than a quiet estate. The top-up only ever appends, and deals in a hash
    order of its own, so a building keeps the pair it was seeded with and adding a building
    to the set never rewrites another building's feed.
    """
    out = {b: faults_for(b) for b in building_ids}
    if not out:
        return out
    order = sorted(building_ids, key=lambda b: h(f"{b}:topup", 1_000_003))
    # A fault sitting on a feed that masks it is not covered — that is how baseload creep
    # went missing while the hash swore it had been dealt.
    covered = {f for v in out.values() for f in v if not CONFLICTS.get(f, set()) & set(v)}
    for f in [f for f in FAULTS if f not in covered]:
        clash = CONFLICTS.get(f, set())
        free = [b for b in order if not clash & set(out[b])]
        bid = (free or order)[h(f"topup:{f}", len(free or order))]
        if f not in out[bid]:
            out[bid].append(f)
    return out


class DetectionSeeder:
    def __init__(self, conn: asyncpg.Connection, apply: bool, regen_gas: bool = False) -> None:
        self.c, self.apply, self.regen_gas = conn, apply, regen_gas
        self.counts: dict[str, int] = {}
        self.plan: list[str] = []

    def note(self, k: str, n: int = 1) -> None:
        self.counts[k] = self.counts.get(k, 0) + n

    # ── the main electricity meter ─────────────────────────────────────────────
    async def main_meter(self, b: dict) -> dict | None:
        row = await self.c.fetchrow("""
            SELECT id, raw_metadata, (SELECT min(period_minutes) FROM plenum_cafm.meter_readings r WHERE r.meter_id = m.id) AS period,
                   coalesce(raw_metadata->>'synthetic','false') = 'true' AS synthetic
              FROM plenum_cafm.energy_meters m
             WHERE m.site_id = $1 AND m.active AND m.meter_type = 'electricity' AND NOT m.is_sub_meter
             ORDER BY created_at LIMIT 1""", b["building_id"])
        if not row:
            return None
        d = dict(row)
        # asyncpg hands jsonb back as text unless a codec is registered.
        if isinstance(d.get("raw_metadata"), str):
            d["raw_metadata"] = json.loads(d["raw_metadata"] or "{}")
        return d

    def hourly_base(self, b: dict, fuel: str) -> float:
        key = eui_key(b["use"])
        elec, gas = ELEC_EUI_BY_USE[key], GAS_EUI_BY_USE[key]
        recorded = float(b["recorded_benchmark"]) if b.get("recorded_benchmark") is not None else None
        has_gas = b["cc"] in ("UK", "GB") and gas > 0
        if fuel == "gas":
            share = gas / (elec + gas)
            base = recorded * share if recorded else gas
        elif has_gas:
            share = elec / (elec + gas)
            base = recorded * share if recorded else elec
        else:
            base = recorded if recorded else (elec + gas)
        factor = 0.80 + 0.55 * hf(f"{b['building_id']}:{fuel}:factor")
        return base * factor * b["gfa"] / 8760.0

    async def regenerate_main(self, b: dict, m: dict, faults: list[str]) -> None:
        """Half-hourly feed with the building's faults, replacing this tool's own rows."""
        hourly = self.hourly_base(b, "electricity")
        cooling = b["cc"] in ("AE", "SG", "US")
        capacity_kw = round(hourly * 1.45 * 1.15 * 1.30, 1)     # 30 % above the normal seasonal peak
        closed_wo_at = None
        if "regress" in faults:
            wo = await self.c.fetchrow("SELECT id FROM plenum_cafm.work_orders WHERE building_id = $1 ORDER BY created_at LIMIT 1", b["building_id"])
            if wo:
                closed_wo_at = (datetime.now(timezone.utc) - timedelta(days=20)).replace(minute=0, second=0, microsecond=0)
            else:
                faults = [f for f in faults if f != "regress"]
        self.plan.append(f"{b['name']}: {', '.join(faults)}" + (" (closes a work order 20 d ago)" if closed_wo_at else ""))
        self.note("main meter regenerated half-hourly", 1)
        self.note("main meter half-hour rows", DAYS * 48)
        if not self.apply:
            return
        meta = dict(m["raw_metadata"] or {})
        meta.update({
            "simulate": True, "synthetic": True, "detection_faults": faults,
            "occupancy_hours": {"start_hour": 7, "end_hour": 19, "weekdays_only": True},
            "tariff_bands": [{"name": "peak", "start_hour": 16, "end_hour": 19, "weekdays_only": True,
                              "rate": round(float(b["tariff"]) * PEAK_PREMIUM, 4)}],
            "offpeak_rate": round(float(b["tariff"]) * OFFPEAK, 4),
            "capacity_kw": capacity_kw,
            "sim_note": f"synthetic demo feed regenerated {date.today().isoformat()} with faults {faults}",
        })
        await self.c.execute("UPDATE plenum_cafm.energy_meters SET raw_metadata = $2::jsonb, updated_at = now() WHERE id = $1",
                             m["id"], json.dumps(meta))
        await self.retire_anomalies(m["id"])
        await self.c.execute("DELETE FROM plenum_cafm.meter_readings WHERE meter_id = $1 AND source = 'simulator'", m["id"])
        await self.c.execute("DELETE FROM plenum_cafm.eui_snapshots WHERE site_id = $1", b["building_id"])
        if closed_wo_at:
            await self.c.execute("""UPDATE plenum_cafm.work_orders SET closed_at = CAST($2 AS timestamptz), completed_at = CAST($2 AS timestamptz)::timestamp, status = 'closed'
                                     WHERE id = (SELECT id FROM plenum_cafm.work_orders WHERE building_id = $1 ORDER BY created_at LIMIT 1)""",
                                 b["building_id"], closed_wo_at)
        f = {k: (k in faults) for k in FAULTS}
        # Every fault is a multiplicative term guarded by its flag, in one generate_series
        # insert. Times are UTC half-hours; "today" is the current UTC date.
        await self.c.execute(f"""
            INSERT INTO plenum_cafm.meter_readings
                (id, organization_id, meter_id, reading_at, period_minutes, consumption_kwh, source, quality_flag, created_at)
            SELECT gen_random_uuid(), CAST($2 AS uuid), CAST($1 AS uuid), ts, 30,
                   round((CASE
                     WHEN CAST($10 AS boolean) AND ts >= now() - interval '6 days' AND ts < now() - interval '6 days' + interval '7 hours'
                        THEN CAST($3 AS float8) / 2 * 0.55              -- flatline: identical reads for 7 hours
                     ELSE CAST($3 AS float8) / 2
                       * (CASE WHEN extract(isodow FROM ts) >= 6
                               THEN (CASE WHEN CAST($4 AS boolean) AND ts >= now() - interval '14 days' THEN 0.98 ELSE 0.45 END)
                               ELSE 1.0 END)
                       * (CASE WHEN extract(hour FROM ts) BETWEEN 7 AND 18 THEN 1.45
                               WHEN CAST($6 AS boolean) AND ts >= now() - interval '8 days' AND extract(hour FROM ts) BETWEEN 4 AND 6
                                    AND extract(isodow FROM ts) < 6 THEN 1.45   -- plant starting at 04:00
                               WHEN CAST($5 AS boolean) AND ts >= now() - interval '10 days' THEN 0.80    -- unoccupied hours loaded
                               ELSE 0.55 END)
                       * (CASE WHEN CAST($7 AS boolean) AND extract(hour FROM ts) < 5 AND ts >= now() - interval '35 days'
                               THEN 1.0 + 0.045 * ceil(extract(epoch FROM ts - (now() - interval '35 days')) / 604800.0)
                               ELSE 1.0 END)                                                           -- overnight creep, week on week
                       * (CASE WHEN CAST($8 AS boolean) AND extract(isodow FROM ts) < 6 AND extract(hour FROM ts) BETWEEN 16 AND 18
                                    AND ts >= now() - interval '14 days' THEN 1.55 ELSE 1.0 END)            -- peak-band load
                       * (CASE WHEN CAST($9 AS boolean) AND ts >= now() - interval '12 days' THEN 1.18 ELSE 1.0 END)   -- step drift
                       * (CASE WHEN CAST($11 AS boolean) AND extract(hour FROM ts) BETWEEN 13 AND 14 AND extract(isodow FROM ts) < 6
                                    AND ts::date IN ((now() - interval '3 days')::date, (now() - interval '6 days')::date, (now() - interval '9 days')::date)
                               THEN 2.6 ELSE 1.0 END)                                                 -- peak excursions
                       * (CASE WHEN CAST($12 AS timestamptz) IS NOT NULL AND ts >= CAST($12 AS timestamptz)
                                    AND ts < CAST($12 AS timestamptz) + interval '8 days' THEN 0.72 ELSE 1.0 END)   -- the fix, then back
                       * (CASE WHEN CAST($13 AS boolean) THEN (0.85 + 0.30 * (1 - cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                               ELSE (0.92 + 0.16 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2) END)
                       * (0.94 + 0.12 * (('x' || substr(md5(CAST($1 AS text) || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)
                   END)::numeric, 3),
                   'simulator',
                   CASE WHEN CAST($10 AS boolean) AND ts::date = (now() - interval '4 days')::date THEN 'estimated' END,
                   now()
              FROM generate_series(date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => {DAYS}),
                                   date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '30 minutes', interval '30 minutes') AS ts
             WHERE NOT (CAST($10 AS boolean) AND ts >= now() - interval '10 days' AND ts < now() - interval '10 days' + interval '4 hours')  -- the gap
            """, str(m["id"]), str(b["organization_id"]) if b["organization_id"] else None, hourly,
            f["weekend"], f["nonocc"], f["schedule"], f["baseload"], f["tou"], f["drift"], f["dataq"], f["peak"],
            closed_wo_at, cooling)

    async def retire_anomalies(self, meter_id) -> None:
        """Findings made in readings that are about to be replaced are closed, not deleted —
        and the approval each one queued is decided the same way, so nothing waits on a
        reading that no longer exists."""
        n = await self.c.execute("""
            UPDATE plenum_cafm.energy_anomalies SET status = 'dismissed',
                   detail_json = coalesce(detail_json, '{}'::jsonb) || '{"dismissed_reason": "synthetic feed regenerated"}'::jsonb
             WHERE meter_id = $1 AND status NOT IN ('resolved','closed','dismissed')""", meter_id)
        await self.c.execute("""
            UPDATE plenum_cafm.approvals_queue_items SET status = 'dismissed', decided_at = now(),
                   pm_notes = coalesce(pm_notes || ' · ', '') || 'superseded: synthetic feed regenerated'
             WHERE status IN ('queued','pending') AND item_type LIKE 'energy_anomaly_%'
               AND summary LIKE '%' || $1::text || '%'""", str(meter_id))
        self.note("stale anomalies dismissed", int(n.split()[-1]) if n else 0)

    async def regenerate_gas(self, b: dict, faults: list[str] | None = None) -> None:
        """The gas feed carried a season so strong the drift rule fired on every UK meter.
        Half-hourly now, a mild winter shape, and a step drift only where the building's
        faults include one — so gas anomalies are findings, not background."""
        m = await self.c.fetchrow("""
            SELECT id, raw_metadata, coalesce(raw_metadata->>'synthetic','false') = 'true' AS synthetic
              FROM plenum_cafm.energy_meters WHERE site_id = $1 AND active AND meter_type = 'gas' AND NOT is_sub_meter
             ORDER BY created_at LIMIT 1""", b["building_id"])
        if not m or not m["synthetic"]:
            return
        meta = m["raw_metadata"]
        meta = json.loads(meta) if isinstance(meta, str) else (meta or {})
        if meta.get("detection_regenerated") and not self.regen_gas:
            return
        drift = "drift" in (faults if faults is not None else faults_for(str(b["building_id"])))
        hourly = self.hourly_base(b, "gas")
        self.note("gas meter regenerated half-hourly")
        self.note("gas half-hour rows", DAYS * 48)
        if not self.apply:
            return
        meta.update({"detection_regenerated": date.today().isoformat(), "simulate": True, "synthetic": True})
        await self.c.execute("UPDATE plenum_cafm.energy_meters SET raw_metadata = $2::jsonb, updated_at = now() WHERE id = $1",
                             m["id"], json.dumps(meta))
        await self.retire_anomalies(m["id"])
        await self.c.execute("DELETE FROM plenum_cafm.meter_readings WHERE meter_id = $1 AND source = 'simulator'", m["id"])
        await self.c.execute(f"""
            INSERT INTO plenum_cafm.meter_readings
                (id, organization_id, meter_id, reading_at, period_minutes, consumption_kwh, source, created_at)
            SELECT gen_random_uuid(), CAST($2 AS uuid), CAST($1 AS uuid), ts, 30,
                   round((CAST($3 AS float8) / 2
                     * (0.70 + 0.60 * (1 + cos(2 * pi() * (extract(doy FROM ts) - 15) / 365.25)) / 2)
                     * (CASE WHEN extract(hour FROM ts) BETWEEN 7 AND 18 THEN 1.25 ELSE 0.6 END)
                     * (CASE WHEN extract(isodow FROM ts) >= 6 THEN 0.7 ELSE 1.0 END)
                     * (CASE WHEN CAST($4 AS boolean) AND ts >= now() - interval '12 days' THEN 1.16 ELSE 1.0 END)
                     * (0.95 + 0.10 * (('x' || substr(md5(CAST($1 AS text) || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)
                   )::numeric, 3), 'simulator', now()
              FROM generate_series(date_trunc('hour', now() AT TIME ZONE 'UTC') - make_interval(days => {DAYS}),
                                   date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '30 minutes', interval '30 minutes') AS ts""",
            str(m["id"]), str(b["organization_id"]) if b["organization_id"] else None, hourly, drift)

    # ── sub-metered assets ─────────────────────────────────────────────────────
    async def sub_meters(self, b: dict, hourly: float, spike: bool) -> None:
        assets = [dict(r) for r in await self.c.fetch(
            "SELECT id::text AS id, coalesce(asset_code, asset_name, id::text) AS label FROM plenum_cafm.assets"
            " WHERE building_id = $1 AND id::text ~ '^[0-9a-f]{8}-' ORDER BY id LIMIT 2", b["building_id"])]
        labels = ["AHU-01 supply fan", "Chiller plant CH-1"]
        for i in range(2):
            asset_id = uuid.UUID(assets[i]["id"]) if i < len(assets) else uuid.uuid5(NS, f"sub-asset:{b['building_id']}:{i}")
            label = assets[i]["label"] if i < len(assets) else labels[i]
            mid = uuid.uuid5(NS, f"submeter:{b['building_id']}:{i}")
            exists = await self.c.fetchval("SELECT count(*) FROM plenum_cafm.energy_meters WHERE id = $1", mid)
            share = 0.18 if i == 0 else 0.12
            with_spike = spike and i == 0
            if exists:
                continue
            self.note("sub-meters created")
            self.note("sub-meter half-hour rows", 60 * 48)
            if with_spike:
                self.note("asset spike injected")
            if not self.apply:
                continue
            await self.c.execute("""
                INSERT INTO plenum_cafm.energy_meters
                    (id, organization_id, site_id, asset_id, meter_type, mpan, tariff_gbp_per_kwh, carbon_kg_per_kwh,
                     is_sub_meter, active, raw_metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, 'electricity', $5, $6, 0.207, true, true, $7::jsonb, now(), now())
                ON CONFLICT (id) DO NOTHING""",
                mid, b["organization_id"], b["building_id"], asset_id, f"SYN-SUB-{str(mid)[:6].upper()}", float(b["tariff"]),
                json.dumps({"simulate": True, "synthetic": True, "asset_label": label,
                            "sim_note": f"synthetic sub-meter on {label}, seeded {date.today().isoformat()}"}))
            await self.c.execute("""
                INSERT INTO plenum_cafm.meter_readings
                    (id, organization_id, meter_id, asset_id, reading_at, period_minutes, consumption_kwh, source, created_at)
                SELECT gen_random_uuid(), CAST($2 AS uuid), CAST($1 AS uuid), CAST($5 AS uuid), ts, 30,
                       round((CAST($3 AS float8) / 2
                         * (CASE WHEN extract(isodow FROM ts) >= 6 THEN 0.5 ELSE 1.0 END)
                         * (CASE WHEN extract(hour FROM ts) BETWEEN 7 AND 18 THEN 1.4 ELSE 0.6 END)
                         * (CASE WHEN CAST($4 AS boolean) AND ts >= now() - interval '3 days' THEN 2.3 ELSE 1.0 END)
                         * (0.95 + 0.10 * (('x' || substr(md5(CAST($1 AS text) || ts::text), 1, 6))::bit(24)::int % 1000) / 1000.0)
                       )::numeric, 3), 'simulator', now()
                  FROM generate_series(date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '60 days',
                                       date_trunc('hour', now() AT TIME ZONE 'UTC') - interval '30 minutes', interval '30 minutes') AS ts""",
                str(mid), str(b["organization_id"]) if b["organization_id"] else None, hourly * share, with_spike, str(asset_id))

    # ── BMS trends, degree days ────────────────────────────────────────────────
    async def bms(self, b: dict) -> None:
        if await self.c.fetchval("SELECT count(*) FROM plenum_cafm.bms_trends WHERE building_id = $1 AND recorded_at >= now() - interval '7 days'", b["building_id"]):
            return
        self.note("BMS trend buildings")
        self.note("BMS trend rows", 7 * 96 * len(ZONES))
        if not self.apply:
            return
        for i, zone in enumerate(ZONES):
            fight = zone == "L2 West"
            await self.c.execute("""
                INSERT INTO plenum_cafm.bms_trends (id, organization_id, building_id, zone, recorded_at, heating_pct, cooling_pct, source, created_at)
                SELECT gen_random_uuid(), CAST($1 AS uuid), CAST($2 AS uuid), $3, ts,
                       CASE WHEN CAST($4 AS boolean) AND extract(hour FROM ts) BETWEEN 13 AND 13 THEN 38
                            WHEN extract(hour FROM ts) BETWEEN 6 AND 9 THEN 45 ELSE 0 END,
                       CASE WHEN CAST($4 AS boolean) AND extract(hour FROM ts) BETWEEN 13 AND 13 THEN 32
                            WHEN extract(hour FROM ts) BETWEEN 12 AND 17 THEN 55 ELSE 0 END,
                       'synthetic', now()
                  FROM generate_series(now() - interval '7 days', now() - interval '15 minutes', interval '15 minutes') AS ts""",
                str(b["organization_id"]) if b["organization_id"] else None, str(b["building_id"]), zone, fight)

    async def degree_days(self, b: dict) -> None:
        if await self.c.fetchval("SELECT count(*) FROM plenum_cafm.weather_degree_days WHERE building_id = $1", b["building_id"]) >= 12:
            return
        self.note("degree-day buildings")
        self.note("degree-day months", 14)
        if not self.apply:
            return
        hot = b["cc"] in ("AE", "SG")
        for k in range(14):
            month = (date.today().replace(day=1) - timedelta(days=1)).replace(day=1)
            for _ in range(k):
                month = (month - timedelta(days=1)).replace(day=1)
            import math
            season = math.cos(2 * math.pi * (month.month - 1) / 12)     # +1 in January
            if b["cc"] == "SG":
                hdd, cdd = 0.0, 240.0 + 15 * season
            elif hot:
                hdd, cdd = max(0.0, 10 * season), 260.0 - 180 * season
            else:
                hdd, cdd = max(0.0, 180.0 + 170 * season), max(0.0, 25.0 - 60 * season)
            await self.c.execute("""
                INSERT INTO plenum_cafm.weather_degree_days (id, organization_id, building_id, month, hdd, cdd, base_temp_c, station, source, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, 15.5, 'synthetic', 'synthetic', now()) ON CONFLICT DO NOTHING""",
                uuid.uuid5(NS, f"dd:{b['building_id']}:{month}"), b["organization_id"], b["building_id"], month,
                round(hdd, 1), round(cdd, 1))

    async def run(self, targets: list[dict]) -> None:
        # First pass: find each building's main meter, so the fault top-up is dealt only
        # among the feeds this tool may rewrite — a fault landed on a real meter is a fault
        # nobody ever sees.
        mains: list[tuple[dict, dict]] = []
        for b in targets:
            b["tariff"] = await self.c.fetchval(
                "SELECT tariff_gbp_per_kwh FROM plenum_cafm.energy_meters WHERE site_id = $1 AND active ORDER BY is_sub_meter, created_at LIMIT 1",
                b["building_id"]) or 0.28
            m = await self.main_meter(b)
            if not m:
                self.plan.append(f"{b['name']}: no electricity meter — run seed_energy_demo first")
                continue
            mains.append((b, m))
        plan = assign_faults([str(b["building_id"]) for b, m in mains if m["synthetic"]])
        for b, m in mains:
            faults = list(plan.get(str(b["building_id"])) or faults_for(str(b["building_id"])))
            if b["cc"] == "AE" or h(f"{b['building_id']}:fight", 3) == 0:
                faults.append("fight")
            if h(f"{b['building_id']}:spike", 2) == 0:
                faults.append("spike")
            already = (m["raw_metadata"] or {}).get("detection_faults")
            want = [f for f in faults if f in FAULTS]
            if "regress" in want and not await self.c.fetchval(
                    "SELECT 1 FROM plenum_cafm.work_orders WHERE building_id = $1 LIMIT 1", b["building_id"]):
                # No work order to close, so the dip is never injected and the marker would
                # never match the plan — which used to re-feed the building on every run.
                want = [f for f in want if f != "regress"]
                faults = [f for f in faults if f != "regress"]
            if m["synthetic"] and set(want) - set(already or []):
                # The marker records what was injected. When the plan gains a fault the
                # feed is re-fed, not patched, so the readings and the marker agree.
                await self.regenerate_main(b, m, want)
            elif already:
                self.plan.append(f"{b['name']}: already seeded ({', '.join(already)})")
            else:
                self.plan.append(f"{b['name']}: real meter, readings left alone")
            await self.regenerate_gas(b, faults)
            await self.sub_meters(b, self.hourly_base(b, "electricity"), "spike" in faults)
            if "fight" in faults:
                await self.bms(b)
            await self.degree_days(b)


async def validate_and_scan(db: str) -> None:
    raw = dsn()
    os.environ["DB_URL"] = re.sub(r"^postgresql://", "postgresql+asyncpg://", raw.split("?")[0]).rsplit("/", 1)[0] + f"/{db}"
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from src import db as _db  # noqa: E402
    from src.engines.energy import anomalies, benchmarks, chiller, detection_coverage  # noqa: E402
    from sqlalchemy import text  # noqa: E402
    from uuid import UUID  # noqa: E402
    async with _db.AsyncSessionLocal() as s:
        ids = [UUID(str(r)) for r in (await s.execute(text(
            "SELECT building_id FROM plenum_cafm.buildings WHERE raw_metadata->>'synthetic_energy_seed' IS NOT NULL"))).scalars().all()]
        rep = await benchmarks.validate(s, building_ids=ids, organization_id=None, persist=True)
        print("  benchmarks:", rep["summary"])
    async with _db.AsyncSessionLocal() as s:
        res = await anomalies.scan_all_active_meters(s, limit=1000)
        print("  anomaly scan: meters", res.get("meters_scanned"))
    async with _db.AsyncSessionLocal() as s:
        ch = await chiller.scan_all(s)
        print("  chiller scan:", [(r["asset_id"][:8], r["ok"], r.get("breach")) for r in ch.get("results", [])])
    async with _db.AsyncSessionLocal() as s:
        cov = await detection_coverage.coverage(s, building_ids=ids)
        print("  coverage:", cov["summary"])
        for r in cov["rules"]:
            print(f"    {r['label']:<34} armed {r['armed']:>2} · fired {r['fired']:>2} · skipped {r['skipped']:>2}"
                  + (f"  ← {list(r['skipped_reasons'])[0][:60]}" if r["skipped_reasons"] else ""))
        for b in cov["buildings"]:
            print(f"    {b['name']:<22} {b['country_code'] or '?':<3} meters {b['meters']} sub {b['sub_meters']} chillers {b['chillers']} · fired: {', '.join(b['fired']) or '—'}")
    await _db.engine.dispose()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--regen-gas", action="store_true", help="regenerate the synthetic gas feeds even if already regenerated")
    ap.add_argument("--validate-only", action="store_true", help="skip seeding; run the scans and the coverage report")
    args = ap.parse_args()
    if args.validate_only:
        await validate_and_scan(args.db)
        return
    conn = await asyncpg.connect(dsn(), database=args.db, timeout=60)
    print(f"== {args.db} — {'APPLY' if args.apply else 'dry run'}")
    base = Seeder(conn, apply=False)
    targets = await base.targets()
    for b in targets:                      # country / use / area, as the energy seeder resolved them (read-only)
        await base.survey(b)
    ds = DetectionSeeder(conn, args.apply, regen_gas=args.regen_gas)
    tx = conn.transaction()
    await tx.start()
    try:
        await ds.run(targets)
        if args.apply:
            await tx.commit()
        else:
            await tx.rollback()
    except Exception:
        await tx.rollback()
        raise
    for line in ds.plan:
        print("  ", line)
    for k, v in sorted(ds.counts.items()):
        print(f"  {k}: {v:,}")
    if not args.apply:
        print("  dry run — nothing written; re-run with --apply")
    await conn.close()
    if args.apply and args.validate:
        await validate_and_scan(args.db)


if __name__ == "__main__":
    asyncio.run(main())
