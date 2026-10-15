"""Give the Maintenance screen every state it can show, and the fields it prints beside them.

The screen bands decisions four ways and the register only ever produced two of them: every
work order was open or closed, so Blocked and Deviation were structurally impossible and the
chips for them were dead. Vendor and estimate printed as em-dashes because neither column was
populated. PPM to plan had nothing to read at all.

What this writes, and why each one is needed rather than nice to have:

* **A status spread.** Blocked and Awaiting approval are statuses; Deviation is not — it is a
  live order past its due date, which needs ``sla_due_at`` set in the past. No amount of
  status seeding produces a Deviation without a due date behind it.
* **Vendor and estimate** on the orders that are still live, because the decision rows print
  both and a queue of unpriced, unassigned work is not a queue anybody can act on.
* **A planned type** on a slice, since nothing could be classified as planned and PPM health
  reads that column to know what a planned visit even is.
* **PPM visits** against the contracts, so PPM to plan has visits to be to plan against.
* **Inspection reports with open recommendations**, which is what Recommendations unconverted
  counts.

Only orders that are still live are touched. Closed and completed orders are history and are
left exactly as they are — a seeder that rewrites what already happened is not seeding, it is
falsification. Every choice is stable by hash, so a second run writes the same thing and the
counts do not drift. Dry run unless ``--apply``.
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
NS = uuid.UUID("2b7f41c8-6a13-4d59-9e0c-5f8a2d7b3e14")

#: The share of still-live orders that lands in each state. The rest stay plainly open,
#: because a queue where everything needs a decision is not a queue either.
BLOCKED_SHARE = 0.15
AWAITING_SHARE = 0.22
DEVIATION_SHARE = 0.12

#: The words this platform reads for each state. Taken from the service's own constants so a
#: seeded row and a real one are read by exactly the same matcher.
STATUS_BLOCKED = "blocked"
STATUS_AWAITING = "pending_approval"
STATUS_LIVE = "open"

#: Live states the seeder is allowed to move. Anything closed or completed is history.
MOVABLE = ("open", "raised", "inprogress", "in progress", "in_progress", "assigned",
           "scheduled", "pending_approval", "blocked", "on hold")

#: What a planned visit is called, so the PPM module can find one.
PLANNED_TYPE = "PPM"
PLANNED_SHARE = 0.25

#: Estimates, by priority band. Round numbers a quantity surveyor would recognise, not noise.
ESTIMATE_BANDS = ((300, 900), (400, 1400), (600, 2600), (900, 4200))

FREQUENCIES = ("Monthly", "Quarterly", "Six-monthly")


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
    """A stable number from the inputs, so a second run writes the same thing."""
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8], 16)


async def has_col(c, table: str, col: str) -> bool:
    return bool(await c.fetchval(
        """SELECT 1 FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name=$1 AND column_name=$2""",
        table, col))


async def run(db: str, apply: bool) -> None:
    c = await asyncpg.connect(dsn_for(db), database=db, timeout=120)
    print(f"\n################ {db} {'(APPLY)' if apply else '(dry run)'} ################")

    has_sla = await has_col(c, "work_orders", "sla_due_at")
    has_wo_type = await has_col(c, "work_orders", "wo_type")
    has_est = await has_col(c, "work_orders", "estimated_cost")
    has_vendor = await has_col(c, "work_orders", "assigned_vendor")

    before = {r["s"]: r["n"] for r in await c.fetch("""
        SELECT lower(coalesce(status,'')) s, count(*) n FROM plenum_cafm.work_orders
         WHERE building_id IS NOT NULL GROUP BY 1 ORDER BY n DESC""")}
    print(f"  BEFORE: {before}")

    live = await c.fetch("""
        SELECT id::text AS id, building_id::text AS building_id, priority
          FROM plenum_cafm.work_orders
         WHERE building_id IS NOT NULL
           AND lower(coalesce(status,'')) = ANY($1::text[])
         ORDER BY id""", list(MOVABLE))
    print(f"  live orders that may be moved: {len(live)} "
          f"(closed and completed are history and are not touched)")
    if not live:
        print("  nothing to seed"); await c.close(); return

    vendors = [r["id"] for r in await c.fetch(
        "SELECT id::text AS id FROM plenum_cafm.vendors ORDER BY id LIMIT 40")]

    # ── the state spread ────────────────────────────────────────────────────────────
    n = len(live)
    n_blocked = int(n * BLOCKED_SHARE)
    n_await = int(n * AWAITING_SHARE)
    n_dev = int(n * DEVIATION_SHARE)
    ordered = sorted(live, key=lambda r: h("state", r["id"]))
    blocked = ordered[:n_blocked]
    awaiting = ordered[n_blocked:n_blocked + n_await]
    deviating = ordered[n_blocked + n_await:n_blocked + n_await + n_dev]
    plain = ordered[n_blocked + n_await + n_dev:]

    if apply:
        await c.execute("""UPDATE plenum_cafm.work_orders SET status = $2
                            WHERE id::text = ANY($1::text[])""",
                        [r["id"] for r in blocked], STATUS_BLOCKED)
        await c.execute("""UPDATE plenum_cafm.work_orders SET status = $2
                            WHERE id::text = ANY($1::text[])""",
                        [r["id"] for r in awaiting], STATUS_AWAITING)
        # A Deviation is a live order past its due date, so it needs both.
        await c.execute("""UPDATE plenum_cafm.work_orders SET status = $2
                            WHERE id::text = ANY($1::text[])""",
                        [r["id"] for r in deviating], STATUS_LIVE)
    print(f"  states: {len(blocked)} blocked · {len(awaiting)} awaiting approval · "
          f"{len(deviating)} to deviate · {len(plain)} left plainly open")

    # ── due dates: the ones that make a Deviation, and the ones that do not ─────────
    due_col = "sla_due_at" if has_sla else "scheduled_date"
    overdue = pending = 0
    for r in live:
        days = h("due", r["id"]) % 40
        is_dev = any(x["id"] == r["id"] for x in deviating)
        when = (datetime.now(timezone.utc) - timedelta(days=3 + days % 25) if is_dev
                else datetime.now(timezone.utc) + timedelta(days=5 + days))
        overdue += 1 if is_dev else 0
        pending += 0 if is_dev else 1
        if not apply:
            continue
        if has_sla:
            await c.execute(f"UPDATE plenum_cafm.work_orders SET {due_col} = $2 WHERE id::text = $1",
                            r["id"], when.replace(tzinfo=None))
        else:
            await c.execute(f"UPDATE plenum_cafm.work_orders SET {due_col} = $2 WHERE id::text = $1",
                            r["id"], when.date().isoformat())
    print(f"  {due_col}: {overdue} set in the past (these become Deviation), "
          f"{pending} set ahead")

    # ── vendor, estimate, planned type ──────────────────────────────────────────────
    priced = assigned = planned = 0
    for r in live:
        seed = h("fields", r["id"])
        if has_vendor and vendors:
            if apply:
                await c.execute("""UPDATE plenum_cafm.work_orders SET assigned_vendor = $2
                                    WHERE id::text = $1 AND assigned_vendor IS NULL""",
                                r["id"], vendors[seed % len(vendors)])
            assigned += 1
        if has_est:
            lo, hi = ESTIMATE_BANDS[seed % len(ESTIMATE_BANDS)]
            amount = round(lo + (hi - lo) * ((seed % 1000) / 1000.0), -1)
            if apply:
                await c.execute("""UPDATE plenum_cafm.work_orders SET estimated_cost = $2
                                    WHERE id::text = $1 AND estimated_cost IS NULL""",
                                r["id"], amount)
            priced += 1
        if has_wo_type and seed % 100 < PLANNED_SHARE * 100:
            if apply:
                await c.execute("""UPDATE plenum_cafm.work_orders SET wo_type = $2
                                    WHERE id::text = $1 AND wo_type IS NULL""",
                                r["id"], PLANNED_TYPE)
            planned += 1
    print(f"  vendor on {assigned} · estimate on {priced} · planned type on {planned} "
          f"(only where the column was empty)")

    await _seed_visits(c, apply)
    await _seed_reports(c, apply)
    await _link_reports_to_visits(c, apply)

    after = {r["s"]: r["n"] for r in await c.fetch("""
        SELECT lower(coalesce(status,'')) s, count(*) n FROM plenum_cafm.work_orders
         WHERE building_id IS NOT NULL GROUP BY 1 ORDER BY n DESC""")}
    print(f"  AFTER:  {after}")
    await c.close()


async def _seed_visits(c, apply: bool) -> None:
    """PPM visits against the contracts, so PPM to plan has a plan to be measured against."""
    if not await has_col(c, "ppm_visits", "asset_id"):
        print("  ppm_visits: absent here"); return
    have = await c.fetchval("SELECT count(*) FROM plenum_cafm.ppm_visits")
    if have:
        print(f"  ppm_visits: {have} already on record, left alone"); return

    assets = await c.fetch("""
        SELECT a.id::text AS id, a.organization_id, a.building_id
          FROM plenum_cafm.assets a WHERE a.building_id IS NOT NULL ORDER BY a.id LIMIT 24""")
    contracts = [r["id"] for r in await c.fetch(
        "SELECT id::text AS id FROM plenum_cafm.vendor_contracts ORDER BY id LIMIT 8")]
    vendors = [r["id"] for r in await c.fetch(
        "SELECT id::text AS id FROM plenum_cafm.vendors ORDER BY id LIMIT 8")]
    if not assets:
        print("  ppm_visits: no asset to book a visit against"); return

    asset_is_uuid = await c.fetchval(
        """SELECT data_type='uuid' FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='ppm_visits' AND column_name='asset_id'""")
    today = date.today()
    made = done = missed = deferred = 0
    for a in assets:
        freq = FREQUENCIES[h("freq", a["id"]) % len(FREQUENCIES)]
        months = {"Monthly": 1, "Quarterly": 3, "Six-monthly": 6}[freq]
        for i in range(1, 9):                       # eight visits back through the year
            when = today - timedelta(days=int(i * months * 30.44))
            if when.year != today.year:
                continue
            seed = h("visit", a["id"], i)
            # A visit is done, missed, or deferred — and a deferral is not a miss.
            outcome = "done" if seed % 10 < 7 else ("deferred" if seed % 10 < 8 else "missed")
            made += 1
            done += outcome == "done"
            missed += outcome == "missed"
            deferred += outcome == "deferred"
            if not apply:
                continue
            aid = uuid.UUID(a["id"]) if asset_is_uuid else a["id"]
            # tolerance_days and raw_metadata are NOT NULL with no default here, so they are
            # given real values rather than left to the table: a visit is allowed to slip a
            # few days against its date and still count as on time, and that window is part
            # of the record rather than a detail the seeder gets to skip.
            late_by = (seed % 4) if outcome == "done" else None
            tolerance = 7 if freq == "Monthly" else 14
            await c.execute("""
                INSERT INTO plenum_cafm.ppm_visits
                    (id, organization_id, asset_id, vendor_id, contract_id, frequency,
                     scheduled_date, completed_date, deferred, status, source,
                     tolerance_days, variance_days, within_tolerance, raw_metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'seed:maintenance-states',
                        $11, $12, $13, '{}'::jsonb)
                ON CONFLICT (id) DO NOTHING""",
                uuid.uuid5(NS, f"visit:{a['id']}:{i}"), a["organization_id"], aid,
                uuid.UUID(vendors[seed % len(vendors)]) if vendors else None,
                uuid.UUID(contracts[seed % len(contracts)]) if contracts else None,
                freq, when,
                (when + timedelta(days=late_by)) if outcome == "done" else None,
                outcome == "deferred", outcome,
                tolerance, late_by,
                (late_by is not None and late_by <= tolerance))
    print(f"  ppm_visits: {made} ({done} done · {missed} missed · {deferred} deferred) "
          f"{'written' if apply else 'would write'}")


async def _seed_reports(c, apply: bool) -> None:
    """Inspection reports, some with a recommendation nobody converted."""
    if not await has_col(c, "inspections", "asset_id"):
        print("  inspections: absent here"); return
    have = await c.fetchval("SELECT count(*) FROM plenum_cafm.inspections")
    if have >= 12:
        print(f"  inspections: {have} already on record, left alone"); return

    # Reports go on the assets that actually had a visit, so the two can be joined. Seeding
    # them against whichever completed order came first put reports and visits on disjoint
    # sets of assets, and the "reports filed" column then measured the seeder rather than the
    # contract. Assets with a completed visit come first; anything else fills the remainder.
    orders = await c.fetch("""
        SELECT w.id::text AS id, coalesce(w.wo_code, w.id::text) AS code,
               w.asset_id::text AS asset_id, w.organization_id,
               EXISTS (SELECT 1 FROM plenum_cafm.ppm_visits v
                        WHERE v.asset_id::text = w.asset_id::text
                          AND v.completed_date IS NOT NULL) AS has_visit
          FROM plenum_cafm.work_orders w
         WHERE w.asset_id IS NOT NULL
           AND lower(coalesce(w.status,'')) IN ('completed','closed','complete','done')
         ORDER BY has_visit DESC, w.id LIMIT 18""")
    if not orders:
        print("  inspections: no completed order to attach a report to"); return
    with_visit = sum(1 for o in orders if o["has_visit"])
    print(f"  inspections: {with_visit} of {len(orders)} target assets that had a visit")

    id_type = await c.fetchval(
        """SELECT data_type FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='id'""")
    id_has_default = await c.fetchval(
        """SELECT column_default IS NOT NULL FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='id'""")
    if id_type != "uuid" and not id_has_default:
        print(f"  inspections: id is {id_type} with no default here — a generated key cannot "
              f"be supplied, so reports are left alone")
        return
    insp_cols = {r["column_name"] for r in await c.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections'""")}
    id_is_uuid = await c.fetchval(
        """SELECT data_type='uuid' FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='id'""")
    asset_is_uuid = await c.fetchval(
        """SELECT data_type='uuid' FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='inspections' AND column_name='asset_id'""")
    FINDINGS = [
        ("Filter change deferred — stock · Static pressure rising",
         "Filter change within 4 weeks", "Medium"),
        ("Combustion efficiency 84% vs 91% commissioned · Flue gas CO 180 ppm",
         "Burner service and flue gas analysis", "High"),
        ("Compressor 2 contactor replaced · Condenser coils fouled",
         "Leak test within 3 months; condenser clean", "High"),
        ("Door operator adjusted · Ropes within wear limits", None, "Low"),
        ("Approach temperature 1.8 °C above design · Cooling tower fill partially blocked",
         "Tower fill clean; VSD cooling check", "Medium"),
    ]
    made = open_recs = 0
    for i, w in enumerate(orders):
        obs, rec, risk = FINDINGS[h("finding", w["id"]) % len(FINDINGS)]
        # Two in three recommendations are left unconverted — that is the figure the page
        # counts, and a register where everything was actioned would show nothing.
        converted = rec is not None and h("conv", w["id"]) % 3 == 0
        made += 1
        open_recs += 1 if rec and not converted else 0
        if not apply:
            continue
        rid = uuid.uuid5(NS, f"report:{w['id']}")
        # inspections is fourteen columns on one database and nine on the other — there is no
        # organization_id here at all — so the insert is built from what the table has rather
        # than from what it ought to have.
        values = {
            **({"id": rid} if id_is_uuid else {}),
            "asset_id": uuid.UUID(w["asset_id"]) if asset_is_uuid else w["asset_id"],
            "inspector": "Site engineer",
            "inspection_date": date.today() - timedelta(days=14 + (h("when", w["id"]) % 120)),
            "finding_type": "Condition",
            "observations": obs,
            "risk_level": risk,
            "corrective_action": bool(rec),
            "recommendation": rec,
            "work_order_id": w["id"],
            "converted_work_order_id": (w["id"] if converted else None),
            "source_file": "seed:maintenance-states",
            "organization_id": w["organization_id"],
            "asset_code": None,
        }
        cols = [k for k in values if k in insp_cols and values[k] is not None or k == "id"]
        cols = [k for k in cols if k in insp_cols]
        ph = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await c.execute(
            f"INSERT INTO plenum_cafm.inspections ({', '.join(cols)}) VALUES ({ph})"
            + (" ON CONFLICT (id) DO NOTHING" if id_is_uuid else ""),
            *[values[k] for k in cols])
    print(f"  inspections: {made} reports, {open_recs} with a recommendation nobody converted "
          f"({'written' if apply else 'would write'})")


async def _link_reports_to_visits(c, apply: bool) -> None:
    """Tie a completed visit to the report that came off it, where one exists on that asset.

    The PPM table prints "10 / 12 reports": visits done against reports actually filed, and
    the gap between them is the point of the column. Without the link every contract reads as
    having filed nothing, which is a different and wrong complaint.
    """
    if not await has_col(c, "ppm_visits", "inspection_id"):
        print("  visit→report link: no inspection_id column here"); return
    link_type = await c.fetchval(
        """SELECT data_type FROM information_schema.columns
            WHERE table_schema='plenum_cafm' AND table_name='ppm_visits'
              AND column_name='inspection_id'""")
    if link_type != "text":
        # ppm_visit_inspection_key.sql widens this to text precisely so a uuid key and an
        # integer sequence can both be held. Until that has run the link cannot be written.
        print(f"  visit→report link: inspection_id is {link_type}, not text — run "
              f"ppm_visit_inspection_key.sql first")
        return
    linked = await c.fetchval(
        "SELECT count(*) FROM plenum_cafm.ppm_visits WHERE inspection_id IS NOT NULL")
    if linked:
        print(f"  visit→report link: {linked} already linked, left alone"); return
    rows = await c.fetch("""
        SELECT v.id AS visit_id, i.id AS inspection_id
          FROM plenum_cafm.ppm_visits v
          JOIN LATERAL (
              SELECT i.id FROM plenum_cafm.inspections i
               WHERE i.asset_id::text = v.asset_id::text
               ORDER BY i.inspection_date DESC LIMIT 1) i ON TRUE
         WHERE v.completed_date IS NOT NULL""")
    # Not every completed visit files a report — that gap is what the column measures.
    pairs = [r for r in rows if h("filed", str(r["visit_id"])) % 10 < 8]
    if apply:
        for r in pairs:
            await c.execute(
                "UPDATE plenum_cafm.ppm_visits SET inspection_id = $2 WHERE id = $1",
                r["visit_id"], str(r["inspection_id"]))
    print(f"  visit→report link: {len(pairs)} of {len(rows)} completed visits filed a report "
          f"({'written' if apply else 'would write'})")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="hoistra_test")
    ap.add_argument("--apply", action="store_true",
                    help="write; without this it reports what it would do and stops")
    a = ap.parse_args()
    await run(a.db, a.apply)


if __name__ == "__main__":
    asyncio.run(main())
