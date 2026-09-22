"""Live sensor readings for the instrumented-asset panel, refreshed on the half hour.

The panel reads the newest row per reading_type and grades it against its band. Its sparkline
wants the last twenty-four. So a single reading per metric, written once by a seeder, gives a
panel that says "live" beside a value that has not moved since the database was filled — the
one detail that gives a demo away.

This writes a new row per instrumented asset per metric every thirty minutes.

**What the values do.** Each metric has a baseline inside its band and a daily shape, because
plant follows occupancy: temperatures and loads climb through the working day and fall back
overnight. On top of that sits a slow drift and a small jitter. Nothing is drawn from a
random number generator at run time — a value is a pure function of (asset, metric, slot), so
the same half hour always produces the same reading and a backfill agrees with what the live
loop would have written.

**What runs out of band.** Not one in five at random, which makes the out-of-band count
flicker between runs and tell no story. Here it follows the asset's condition grade: the
worse the grade, the more of its metrics sit outside their limits, and which ones is fixed
per asset. That is the same signal the failure model already weighs, so a card reading
"grade 5, 2 of 8 out of band, 38.8%" is internally consistent rather than three unrelated
numbers. A borderline metric still crosses back and forth on the drift, which is what a real
one does.

Modes:

    --backfill 2        write the last 2 days of half hours, so sparklines have points
    --once              write the current half hour and stop  (for Task Scheduler / cron)
    --loop              write, sleep to the next half hour, repeat
    --prune-days 30     delete readings older than this before writing

Defaults to a dry run. Refuses to touch plenum_agent.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import math
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")
PRODUCTION = "plenum_agent"
SLOT_MINUTES = 30

#: Which metrics a class of plant carries, and the unit each is recorded in. Kept here rather
#: than imported so this script runs against a register seeded by either seeder.
SENSORS = {
    "chiller": ["temperature", "temperature_supply", "temperature_return", "pressure",
                "pressure_discharge", "oil_pressure", "vibration", "load_percentage"],
    "crac": ["temperature", "temperature_supply", "temperature_return", "pressure",
             "pressure_discharge", "oil_pressure", "vibration", "load_percentage"],
    "boiler": ["temperature", "temperature_supply", "temperature_return", "pressure",
               "load_percentage"],
    "ahu": ["temperature", "temperature_supply", "temperature_return", "vibration",
            "load_percentage"],
    "pump": ["temperature", "pressure", "oil_pressure", "vibration"],
    "generator": ["coolant_temp", "oil_pressure", "fuel_level", "exhaust_temp",
                  "battery_voltage", "frequency", "voltage_output", "load_percentage"],
    "fcu": ["temperature", "temperature_supply", "temperature_return"],
}

#: How each metric behaves through a day. `swing` is the fraction of the band width the daily
#: shape moves it by; `peak_hour` is when it tops out. A supply temperature barely moves, a
#: load percentage moves a lot, a battery float voltage should not move at all.
SHAPE = {
    "temperature": (0.22, 15),
    "temperature_supply": (0.10, 15),
    "temperature_return": (0.14, 15),
    "coolant_temp": (0.18, 15),
    "pressure": (0.10, 14),
    "pressure_discharge": (0.20, 15),
    "oil_pressure": (0.08, 14),
    "vibration": (0.16, 15),
    "frequency": (0.04, 12),
    "voltage": (0.06, 19),
    "voltage_output": (0.06, 19),
    "battery_voltage": (0.02, 12),
    "load_percentage": (0.35, 14),
    "fuel_level": (0.05, 12),
    "exhaust_temp": (0.25, 15),
}
DEFAULT_SHAPE = (0.12, 14)


def utcnow() -> datetime:
    """Naive UTC, because asset_readings.recorded_at is a timestamp without time zone."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dsn_for() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def h(*parts: object) -> int:
    """A stable number from the inputs, so the same slot always reads the same."""
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def unit_frac(*parts: object) -> float:
    """That number as a fraction in [0, 1)."""
    return (h(*parts) % 10_000) / 10_000.0


def class_of(asset_name: str, asset_code: str) -> str | None:
    """The class of plant, from the name the register actually holds."""
    hay = f"{asset_name} {asset_code}".lower()
    for key, words in (
        ("chiller", ("chiller", "-ch-")),
        ("crac", ("crac", "acu", "-crc-", "precision cooling")),
        ("boiler", ("boiler", "-blr-")),
        ("ahu", ("ahu", "air handling", "-ahu-")),
        ("fcu", ("fcu", "fan coil", "-fcu-")),
        ("pump", ("pump", "-pmp-")),
        ("generator", ("generator", "-gen-")),
    ):
        if any(w in hay for w in words):
            return key
    return None


def out_of_band_metrics(asset_code: str, grade: int | None, metrics: list[str]) -> set[str]:
    """Which of this asset's metrics sit outside their limits.

    Grade 1 and 2 are sound and nothing is out. From grade 3 up, one more metric goes out per
    grade — so a grade 5 with eight sensors reads "3 of 8", a grade 4 reads "2 of 8". Which
    metrics is fixed per asset, so the panel does not reshuffle between refreshes.
    """
    n = max(0, (grade or 1) - 2)
    if n <= 0 or not metrics:
        return set()
    ranked = sorted(metrics, key=lambda m: h("oob", asset_code, m))
    return set(ranked[:min(n, len(metrics))])


def reading_for(
    *, asset_code: str, metric: str, lo: float, hi: float, slot: datetime,
    outside: bool, grade: int | None,
) -> float:
    """One value, as a pure function of the asset, the metric and the half hour.

    Inside the band it sits between a fifth and four fifths of the width, so an in-band value
    never looks suspiciously pinned to a limit. Outside, it clears the nearer limit by a few
    per cent — enough to grade as out, not so far that it reads as a sensor fault.
    """
    span = max(hi - lo, 1e-6)
    swing, peak_hour = SHAPE.get(metric, DEFAULT_SHAPE)

    # Where in the day we are: a cosine peaking at peak_hour.
    minutes = slot.hour * 60 + slot.minute
    day = math.cos((minutes / 1440.0 - peak_hour / 24.0) * 2 * math.pi)

    # A slow drift over about nine days, so consecutive readings move without sawtoothing.
    drift = math.sin(slot.timestamp() / (9 * 86400.0) * 2 * math.pi
                     + unit_frac("drift", asset_code, metric) * 2 * math.pi)

    jitter = (unit_frac("jitter", asset_code, metric, slot.isoformat()) - 0.5) * 0.04

    if outside:
        # Above the top for most metrics; below the bottom for the ones where "too little" is
        # the fault — a fuel level or an oil pressure fails low, not high.
        low_side = metric in ("fuel_level", "oil_pressure", "battery_voltage", "load_percentage")
        over = 0.04 + 0.05 * abs(drift) + ((grade or 3) - 2) * 0.01
        val = lo - span * over if low_side else hi + span * over
    else:
        base = 0.20 + 0.60 * unit_frac("base", asset_code, metric)
        pos = base + swing * day * 0.5 + 0.05 * drift + jitter
        val = lo + span * min(max(pos, 0.04), 0.96)

    return round(val, 2)


def slots_between(start: datetime, end: datetime) -> list[datetime]:
    step = timedelta(minutes=SLOT_MINUTES)
    out, t = [], start
    while t <= end:
        out.append(t)
        t += step
    return out


def snap(t: datetime) -> datetime:
    """The half hour this moment falls in."""
    return t.replace(minute=0 if t.minute < SLOT_MINUTES else SLOT_MINUTES,
                     second=0, microsecond=0)


async def load_targets(c: asyncpg.Connection) -> list[dict]:
    """Every asset that carries sensors, with what it needs to produce a reading."""
    rows = await c.fetch("""
        SELECT a.id::text AS id, a.organization_id, a.asset_name, a.asset_code,
               a.condition_score, b.name AS building
          FROM plenum_cafm.assets a
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.building_id IS NOT NULL
         ORDER BY a.asset_code""")
    out = []
    for r in rows:
        cls = class_of(r["asset_name"] or "", r["asset_code"] or "")
        if not cls:
            continue
        out.append({**dict(r), "cls": cls, "metrics": SENSORS[cls]})
    return out


async def load_bands(c: asyncpg.Connection) -> dict[str, tuple[float, float, str]]:
    rows = await c.fetch("""
        SELECT reading_type, unit, lo, hi FROM plenum_cafm.asset_reading_bands
         WHERE asset_id IS NULL AND asset_category IS NULL""")
    return {r["reading_type"]: (float(r["lo"]), float(r["hi"]), r["unit"]) for r in rows}


async def write_slots(
    c: asyncpg.Connection, targets: list[dict], bands: dict, slots: list[datetime], apply: bool,
) -> int:
    ins = """
        INSERT INTO plenum_cafm.asset_readings
               (id, organization_id, asset_id, reading_type, value, unit, recorded_at)
        VALUES ($1, $2, $3::uuid, $4, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value, unit = EXCLUDED.unit
    """
    n = 0
    for t in targets:
        outside = out_of_band_metrics(t["asset_code"], t["condition_score"], t["metrics"])
        for metric in t["metrics"]:
            band = bands.get(metric)
            if band is None:
                continue                      # no limit on record, so nothing to grade against
            lo, hi, unit = band
            for slot in slots:
                val = reading_for(asset_code=t["asset_code"], metric=metric, lo=lo, hi=hi,
                                  slot=slot, outside=metric in outside,
                                  grade=t["condition_score"])
                n += 1
                if not apply:
                    continue
                rid = uuid.uuid5(NS, f"reading:{t['id']}:{metric}:{slot.isoformat()}")
                await c.execute(ins, rid, t["organization_id"], t["id"], metric,
                                Decimal(str(val)), unit, slot)
    return n


async def prune(c: asyncpg.Connection, days: int, apply: bool) -> int:
    cutoff = utcnow() - timedelta(days=days)
    if not apply:
        return await c.fetchval(
            "SELECT count(*) FROM plenum_cafm.asset_readings WHERE recorded_at < $1", cutoff)
    tag = await c.execute(
        "DELETE FROM plenum_cafm.asset_readings WHERE recorded_at < $1", cutoff)
    return int(tag.rsplit(" ", 1)[-1] or 0)


async def run(args: argparse.Namespace) -> None:
    if args.db == PRODUCTION:
        raise SystemExit(
            f"refusing to write to {PRODUCTION}. These are generated sensor readings; "
            f"they belong in a test database.")
    c = await asyncpg.connect(dsn_for(), database=args.db, timeout=90)
    apply = args.apply
    print(f"\n################ {args.db} ({'APPLY' if apply else 'dry run'}) ################")

    targets = await load_targets(c)
    bands = await load_bands(c)
    if not targets:
        raise SystemExit("no assets carry sensors. Run seed_northbridge_assets.py first.")
    if not bands:
        raise SystemExit("no global reading bands on record. Run seed_northbridge_assets.py first.")

    print(f"  instrumented assets: {len(targets)}   bands on record: {len(bands)}")
    for t in targets:
        oob = out_of_band_metrics(t["asset_code"], t["condition_score"], t["metrics"])
        graded = [m for m in t["metrics"] if m in bands]
        print(f"    {t['asset_code']:<12} {t['cls']:<10} grade {t['condition_score']}  "
              f"{len(oob)} of {len(graded)} out of band")

    if args.prune_days:
        n = await prune(c, args.prune_days, apply)
        print(f"  pruned older than {args.prune_days}d: {n} rows "
              f"({'deleted' if apply else 'would delete'})")

    now = snap(utcnow())
    if args.backfill:
        slots = slots_between(now - timedelta(days=args.backfill), now)
        n = await write_slots(c, targets, bands, slots, apply)
        print(f"  backfill {args.backfill}d: {len(slots)} slots, {n} readings "
              f"({'written' if apply else 'would write'})")

    if args.loop:
        print(f"  loop: writing every {SLOT_MINUTES} minutes. Ctrl-C to stop.")
        while True:
            slot = snap(utcnow())
            n = await write_slots(c, targets, bands, [slot], apply)
            print(f"  [{utcnow():%H:%M:%S}] slot {slot:%Y-%m-%d %H:%M} -> {n} readings")
            nxt = slot + timedelta(minutes=SLOT_MINUTES)
            await asyncio.sleep(max((nxt - utcnow()).total_seconds(), 5))
    elif args.once or not args.backfill:
        n = await write_slots(c, targets, bands, [now], apply)
        print(f"  slot {now:%Y-%m-%d %H:%M}: {n} readings "
              f"({'written' if apply else 'would write'})")

    if apply:
        after = await c.fetchrow("""
            SELECT count(*) n, count(DISTINCT asset_id) a, max(recorded_at) newest
              FROM plenum_cafm.asset_readings""")
        print(f"\n  AFTER: readings={after['n']} across {after['a']} assets, "
              f"newest {after['newest']}")
    else:
        print("\n  dry run. Nothing was written. Add --apply.")
    await c.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Refresh live asset readings on the half hour.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    ap.add_argument("--backfill", type=float, metavar="DAYS",
                    help="also write this many days of history, so sparklines have points")
    ap.add_argument("--once", action="store_true", help="write the current half hour and stop")
    ap.add_argument("--loop", action="store_true", help="keep writing every half hour")
    ap.add_argument("--prune-days", type=int, metavar="DAYS",
                    help="delete readings older than this first")
    args = ap.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n  stopped.")


if __name__ == "__main__":
    main()
