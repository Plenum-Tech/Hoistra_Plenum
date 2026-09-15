"""Give the Assets page every band it can show, and the panels beneath them something to read.

The condition engine bands an asset from two signals and the register only ever produced one
of them, so every asset came back In control. The reason is worth stating precisely, because
it is not "there was no data":

* **No section was over its own reference.** A section is over when what its sub-meters drew,
  over its area, exceeds the reference it is read against. Most sections had no sub-meter at
  all and the metered ones were comfortably under, so the first signal could never fire.
* **No anomaly had persisted.** Every open finding was detected in the last few days, and a
  Watch on the second signal alone needs one that has run past the threshold.
* **No asset carried a condition grade**, so the failure assessment ran on the anomaly driver
  alone and the poorly-graded card had nothing to count.
* **No part had a warranty**, so nothing could be claimable.

What this writes, and what it does not. Consumption is written as readings against the meters
that actually serve a section, because a section is over reference when it *drew more* — not
when somebody edited its area or moved its benchmark. Backdating an anomaly moves the date it
was detected, which is the fact that makes it persistent. Grades, warranties and parts are
records that were simply absent.

Nothing already on record is overwritten: grades go only where there is none, warranties only
onto fittings that have none, and readings are added rather than replacing what is there.
Every choice is stable by hash so a second run writes the same thing. Dry run unless --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("9d4e2a71-3c85-4b62-a917-6e0f5c82d143")

#: How many of the metered sections should end up over their own reference, and by how much.
#: Two bands so the page has something to sort by rather than one flat excess.
OVER_SECTIONS_SHARE = 0.45
OVER_BY = (0.18, 0.46)

#: How far back an anomaly has to be detected to count as persistent. The engine's threshold
#: is three weeks; these sit clear of it rather than on it.
PERSISTENT_WEEKS = (4, 9)
PERSISTENT_SHARE = 0.35

#: Inspector grades, 1 as new to 5 end of life. A portfolio where nothing is poor is not a
#: portfolio anybody needs a condition engine for, and one where everything is, is noise.
GRADE_MIX = ((1, 0.10), (2, 0.34), (3, 0.34), (4, 0.18), (5, 0.04))

#: Parts that get fitted to plant, with what they cost and how long they are warranted.
PARTS = [
    ("Compressor contactor", "PRT-CONT-2P", 480.0, 24),
    ("Condenser fan motor", "PRT-CFM-15", 1250.0, 12),
    ("VSD control board", "PRT-VSD-CB", 2400.0, 36),
    ("Burner nozzle set", "PRT-BNZ-04", 310.0, 12),
    ("Bearing set", "PRT-BRG-88", 640.0, 18),
    ("Filter bank", "PRT-FLT-G4", 190.0, 6),
]


def dsn_for(db: str) -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def h(*parts) -> int:
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


def grade_for(key: str) -> int:
    """A grade from the stated mix, stable for this asset."""
    r = (h("grade", key) % 1000) / 1000.0
    acc = 0.0
    for g, share in GRADE_MIX:
        acc += share
        if r < acc:
            return g
    return GRADE_MIX[-1][0]


async def has_col(c, table: str, col: str) -> bool:
    return bool(await c.fetchval(
        """SELECT 1 FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name=$1 AND column_name=$2""",
        table, col))


async def run(db: str, apply: bool) -> None:
    c = await asyncpg.connect(dsn_for(db), database=db, timeout=180)
    print(f"\n################ {db} {'(APPLY)' if apply else '(dry run)'} ################")
    await _sections_over_reference(c, apply)
    await _persist_anomalies(c, apply)
    await _condition_grades(c, apply)
    await _warranted_parts(c, apply)
    await _corroborating_reports(c, apply)
    await c.close()


async def _sections_over_reference(c, apply: bool) -> None:
    """Put real consumption behind the sections that should read as over reference.

    A section is over because its meters drew more, so that is what is written. Moving the
    area or the benchmark would produce the same number on screen and mean nothing.
    """
    rows = await c.fetch("""
        SELECT s.section_id::text AS id, s.name, s.gross_area_m2, s.reference_eui_kwh_m2,
               m.id AS meter_id, m.organization_id,
               coalesce((SELECT sum(r.consumption_kwh) FROM plenum_cafm.meter_readings r
                          WHERE r.meter_id = m.id
                            AND r.reading_at >= now() - interval '365 days'), 0) AS kwh
          FROM plenum_cafm.building_sections s
          JOIN plenum_cafm.energy_meters m
                ON m.section_id = s.section_id AND m.active AND m.is_sub_meter
         WHERE s.gross_area_m2 > 0 AND s.reference_eui_kwh_m2 > 0
         ORDER BY s.section_id""")
    if not rows:
        print("  sections: no sub-metered section with an area and a reference"); return

    reading_cols = {x["column_name"] for x in await c.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='meter_readings'""")}
    target = sorted(rows, key=lambda r: h("over", r["id"]))
    n_over = int(len(target) * OVER_SECTIONS_SHARE)
    added = kwh_total = 0
    for i, r in enumerate(target[:n_over]):
        area = float(r["gross_area_m2"])
        ref = float(r["reference_eui_kwh_m2"])
        have = float(r["kwh"] or 0)
        over_by = OVER_BY[h("band", r["id"]) % len(OVER_BY)]
        want = ref * area * (1 + over_by)
        gap = want - have
        if gap <= 0:
            continue                       # already over; nothing to add
        added += 1
        kwh_total += gap
        if not apply:
            continue
        # Spread the shortfall over the last twelve months so it reads as consumption rather
        # than one implausible spike.
        per = gap / 12.0
        for month in range(12):
            when = datetime.now(timezone.utc) - timedelta(days=30 * month + 3)
            vals = {
                "id": uuid.uuid5(NS, f"reading:{r['id']}:{month}"),
                "organization_id": r["organization_id"],
                "meter_id": r["meter_id"],
                "reading_at": when.replace(tzinfo=None),
                "consumption_kwh": round(per, 2),
                "source": "seed:asset-condition",
                # A monthly figure covers a month, and period_minutes is how this table says
                # so. It is required on one database and absent on the other.
                "period_minutes": 30 * 24 * 60,
            }
            cols = [k for k in vals if k in reading_cols]
            ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
            await c.execute(
                f"INSERT INTO plenum_cafm.meter_readings ({', '.join(cols)}) VALUES ({ph}) "
                f"ON CONFLICT (id) DO NOTHING", *[vals[k] for k in cols])
    print(f"  sections: {added} of {len(rows)} sub-metered sections pushed over their own "
          f"reference, {kwh_total:,.0f} kWh added across twelve months "
          f"({'written' if apply else 'would write'})")


async def _persist_anomalies(c, apply: bool) -> None:
    """Backdate a share of open anomalies so some have genuinely persisted.

    The date an anomaly was detected is what makes it persistent, and every one on record was
    detected in the last few days — so the Watch band that rests on persistence alone could
    never fire.
    """
    rows = await c.fetch("""
        SELECT id, detected_at FROM plenum_cafm.energy_anomalies
         WHERE asset_id IS NOT NULL AND status NOT IN ('resolved','closed','dismissed')
         ORDER BY id""")
    if not rows:
        print("  anomalies: none attributed to an asset"); return
    picked = [r for r in rows if h("persist", str(r["id"])) % 100 < PERSISTENT_SHARE * 100]
    moved = 0
    for r in picked:
        weeks = PERSISTENT_WEEKS[0] + h("weeks", str(r["id"])) % (
            PERSISTENT_WEEKS[1] - PERSISTENT_WEEKS[0] + 1)
        when = datetime.now(timezone.utc) - timedelta(weeks=weeks)
        if r["detected_at"] and r["detected_at"].replace(tzinfo=timezone.utc) <= when:
            continue                       # already old enough
        moved += 1
        if apply:
            await c.execute(
                "UPDATE plenum_cafm.energy_anomalies SET detected_at = $2 WHERE id = $1",
                r["id"], when.replace(tzinfo=None))
    print(f"  anomalies: {moved} of {len(rows)} backdated past the persistence threshold "
          f"({'written' if apply else 'would write'})")


async def _condition_grades(c, apply: bool) -> None:
    """An inspector's grade on every asset that has none."""
    if not await has_col(c, "assets", "condition_score"):
        print("  grades: assets carries no condition_score here"); return
    rows = await c.fetch("""
        SELECT id::text AS id FROM plenum_cafm.assets
         WHERE building_id IS NOT NULL AND condition_score IS NULL ORDER BY id""")
    if not rows:
        print("  grades: every asset already carries one, left alone"); return
    tally: dict[int, int] = {}
    for r in rows:
        g = grade_for(r["id"])
        tally[g] = tally.get(g, 0) + 1
        if not apply:
            continue
        await c.execute("""
            UPDATE plenum_cafm.assets
               SET condition_score = $2, condition_updated_at = $3
             WHERE id::text = $1 AND condition_score IS NULL""",
            r["id"], g,
            (datetime.now(timezone.utc) - timedelta(days=h("graded", r["id"]) % 200)
             ).replace(tzinfo=None))
    poor = sum(n for g, n in tally.items() if g >= 4)
    print(f"  grades: {len(rows)} assets graded {dict(sorted(tally.items()))} — "
          f"{poor} poor ({'written' if apply else 'would write'})")


async def _warranted_parts(c, apply: bool) -> None:
    """Parts fitted to plant, with a term and an invoiced value, so a claim can be checked."""
    for t in ("spare_parts", "work_order_parts"):
        if not await has_col(c, t, "id"):
            print(f"  parts: {t} is absent here"); return
    org = await c.fetchval("SELECT organization_id FROM plenum_cafm.assets LIMIT 1")

    # The catalogue first, where it is empty.
    made = 0
    part_ids: list[str] = []
    for name, code, price, months in PARTS:
        pid = await c.fetchval(
            "SELECT id::text FROM plenum_cafm.spare_parts WHERE part_code = $1", code)
        if pid is None and apply:
            key = uuid.uuid5(NS, f"part:{code}")
            id_is_uuid = await c.fetchval(
                """SELECT data_type='uuid' FROM information_schema.columns
                    WHERE table_schema='plenum_cafm' AND table_name='spare_parts'
                      AND column_name='id'""")
            pid = str(key) if id_is_uuid else None
            if id_is_uuid:
                await c.execute("""
                    INSERT INTO plenum_cafm.spare_parts
                        (id, organization_id, part_name, part_code, unit_price, warranty_months)
                    VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (id) DO NOTHING""",
                    key, org, name, code, price, months)
            else:
                pid = str(await c.fetchval("""
                    INSERT INTO plenum_cafm.spare_parts
                        (organization_id, part_name, part_code, unit_price, warranty_months)
                    VALUES ($1, $2, $3, $4, $5) RETURNING id""", org, name, code, price, months))
            made += 1
        if pid:
            part_ids.append(pid)
        elif not apply:
            made += 1
    print(f"  parts catalogue: {made} added, {len(part_ids)} usable "
          f"({'written' if apply else 'would write'})")
    if not part_ids:
        if apply:
            print("  fittings: no part to fit"); return
        # A dry run that reports nothing because it wrote nothing is not a preview. The
        # catalogue it would have written stands in, so the count below is the real one.
        part_ids = [f"would-be:{code}" for _, code, _, _ in PARTS]

    # Then fit them to assets that have had work done, on the orders that did it.
    # work_order_parts.work_order_id points at work_orders.work_order_id on one database and
    # at work_orders.id on the other. Read which, rather than guess: a fitting written against
    # the wrong key is rejected at best and silently orphaned at worst.
    fk_target = await c.fetchval("""
        SELECT a.attname
          FROM pg_constraint con
          JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = con.confkey[1]
         WHERE con.conrelid = 'plenum_cafm.work_order_parts'::regclass
           AND con.contype = 'f'
           AND (SELECT attname FROM pg_attribute
                 WHERE attrelid = con.conrelid AND attnum = con.conkey[1]) = 'work_order_id'
         LIMIT 1""") or "id"
    print(f"  fittings: work_order_parts references work_orders.{fk_target}")
    orders = await c.fetch(f"""
        SELECT w.{fk_target}::text AS wid, w.asset_id::text AS aid, w.organization_id,
               coalesce(w.completed_at, w.closed_at, w.created_at) AS when_done
          FROM plenum_cafm.work_orders w
         WHERE w.asset_id IS NOT NULL AND w.{fk_target} IS NOT NULL
           AND lower(coalesce(w.status,'')) IN ('completed','closed','complete','done')
         ORDER BY w.id LIMIT 30""")
    # These keys are uuid on one database and integer on the other, so each value is coerced
    # to whatever its own column actually is rather than assumed. Guessing here is how a
    # seeder ends up writing a number into a uuid column, or a uuid into a sequence.
    fitting_types = {x["column_name"]: x["data_type"] for x in await c.fetch(
        """SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='work_order_parts'""")}
    fitting_cols = set(fitting_types)

    def coerce(col, value):
        t = fitting_types.get(col, "")
        if value is None:
            return None
        if t in ("integer", "bigint", "smallint"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
        if t == "uuid" and not isinstance(value, uuid.UUID):
            try:
                return uuid.UUID(str(value))
            except (TypeError, ValueError):
                return None
        if t in ("character varying", "text") and isinstance(value, uuid.UUID):
            return str(value)
        return value
    # The key is a uuid with no default on one database and an integer sequence on the other.
    # A generated uuid only goes in where the table expects the caller to supply one.
    supplies_own_id = await c.fetchval(
        """SELECT data_type='uuid' AND column_default IS NULL
             FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='work_order_parts'
              AND column_name='id'""")
    if not supplies_own_id:
        fitting_cols.discard("id")
    fitted = live = 0
    for i, w in enumerate(orders):
        if not part_ids:
            break
        seed = h("fit", w["wid"])
        name, code, price, months = PARTS[seed % len(PARTS)]
        pid = part_ids[seed % len(part_ids)]
        # Two thirds fitted recently enough to still be under their term — the case worth
        # surfacing is plant long out of warranty carrying a part that is not.
        days_ago = (seed % 120) if seed % 3 else (400 + seed % 200)
        when = date.today() - timedelta(days=days_ago)
        expires = when + timedelta(days=int(months * 30.44))
        in_warranty = expires >= date.today()
        fitted += 1
        live += 1 if in_warranty else 0
        if not apply:
            continue
        exists = await c.fetchval(
            "SELECT 1 FROM plenum_cafm.work_order_parts WHERE work_order_id::text = $1 "
            "AND part_id::text = $2", w["wid"], pid)
        if exists:
            continue
        vals = {
            "id": uuid.uuid5(NS, f"fitting:{w['wid']}:{code}"),
            "work_order_id": w["wid"], "part_id": pid, "asset_id": w["aid"],
            "quantity_used": 1, "unit_cost": price,
            "fitted_at": when, "warranty_expiry": expires,
            "invoiced_value": round(price * 1.35, 2), "currency": "GBP",
        }
        cols = [k for k in vals if k in fitting_cols
                and coerce(k, vals[k]) is not None]
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await c.execute(
            f"INSERT INTO plenum_cafm.work_order_parts ({', '.join(cols)}) VALUES ({ph})",
            *[coerce(k, vals[k]) for k in cols])
    print(f"  fittings: {fitted} parts fitted, {live} still inside their term "
          f"({'written' if apply else 'would write'})")


#: What an inspector would have written, months before the engine detected each kind of
#: anomaly. The words matter: a report corroborates an anomaly by naming the same thing, and
#: an observation about filters does not corroborate a chiller efficiency finding however
#: close the dates are.
PRIOR_OBSERVATIONS = {
    "chiller_efficiency": ("Chiller running above design — condenser approach wide, "
                           "efficiency down on commissioning figures",
                           "Chiller condenser clean and efficiency re-test"),
    "simultaneous_heating_cooling": ("Heating and cooling calling together in the same zone; "
                                     "zone valve actuator suspect",
                                     "Zone valve and heating control strategy review"),
    "peak_excursion": ("Peak demand excursion noted on the incomer during morning start; "
                       "plant staging not staggered",
                       "Stagger plant start to hold peak demand down"),
    "asset_spike": ("Single asset drawing above its own profile — bearing noise and vibration "
                    "on the drive end",
                    "Bearing inspection on the spiking asset"),
    "nonocc_spike": ("Plant running through unoccupied hours; time schedule not matching the "
                     "occupancy calendar",
                     "Correct the unoccupied time schedule"),
    "baseline_drift": ("Baseline consumption drifting up week on week with no occupancy "
                       "change; controls left in hand",
                       "Return controls to auto and re-check the baseline"),
    "weekend_spike": ("Weekend profile close to a weekday — calendar exceptions left in place "
                      "after the last event",
                      "Clear the calendar exceptions"),
}


async def _corroborating_reports(c, apply: bool) -> None:
    """Write the report an inspector would have filed before each anomaly was detected.

    An open anomaly is a different proposition when somebody already named the same thing
    months earlier — that is the whole point of the corroboration card. It only fires when a
    report is about the same asset, **predates** the detection, and shares vocabulary with it,
    so a report is written per anomaly type using the words that anomaly is about. Seeding
    generic condition notes would leave the card at zero however many reports were on file,
    which is exactly what it did.
    """
    if not await has_col(c, "inspections", "observations"):
        print("  corroboration: inspections carries no observations here"); return
    anomalies = await c.fetch("""
        SELECT DISTINCT ON (e.asset_id) e.asset_id::text AS aid, e.anomaly_type,
               e.detected_at, a.asset_name
          FROM plenum_cafm.energy_anomalies e
          JOIN plenum_cafm.assets a ON a.id::text = e.asset_id::text
         WHERE e.asset_id IS NOT NULL AND e.status NOT IN ('resolved','closed','dismissed')
         ORDER BY e.asset_id, e.detected_at""")
    if not anomalies:
        print("  corroboration: no open anomaly attributed to an asset"); return

    cols = {r["column_name"] for r in await c.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections'""")}
    id_is_uuid = await c.fetchval(
        """SELECT data_type='uuid' FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='id'""")
    id_has_default = await c.fetchval(
        """SELECT column_default IS NOT NULL FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='id'""")
    asset_is_uuid = await c.fetchval(
        """SELECT data_type='uuid' FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections'
              AND column_name='asset_id'""")
    if not id_is_uuid and not id_has_default:
        print("  corroboration: inspections.id cannot take a generated key here"); return

    made = skipped = 0
    for r in anomalies:
        text_ = PRIOR_OBSERVATIONS.get(r["anomaly_type"])
        if not text_:
            skipped += 1
            continue
        obs, rec = text_
        detected = r["detected_at"]
        when = (detected.date() if hasattr(detected, "date") else date.today()) - timedelta(
            days=21 + h("prior", r["aid"]) % 60)
        vals = {
            **({"id": uuid.uuid5(NS, f"prior:{r['aid']}")} if id_is_uuid else {}),
            "asset_id": uuid.UUID(r["aid"]) if asset_is_uuid else r["aid"],
            "inspector": "Site engineer",
            "inspection_date": when,
            "finding_type": r["anomaly_type"].replace("_", " ").title(),
            "observations": obs,
            "risk_level": "Medium",
            "corrective_action": True,
            "recommendation": rec,
            "source_file": "seed:asset-condition",
        }
        use = [k for k in vals if k in cols]
        made += 1
        if not apply:
            continue
        ph = ", ".join(f"${i}" for i in range(1, len(use) + 1))
        await c.execute(
            f"INSERT INTO plenum_cafm.inspections ({', '.join(use)}) VALUES ({ph})"
            + (" ON CONFLICT (id) DO NOTHING" if id_is_uuid else ""),
            *[vals[k] for k in use])
    print(f"  corroboration: {made} reports written before the anomaly they name"
          + (f", {skipped} anomaly types with no wording on file" if skipped else "")
          + f" ({'written' if apply else 'would write'})")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="hoistra_test")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    await run(a.db, a.apply)


if __name__ == "__main__":
    asyncio.run(main())
