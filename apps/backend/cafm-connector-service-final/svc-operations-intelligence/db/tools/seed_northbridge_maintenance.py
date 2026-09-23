"""The Maintenance page, from the tables it actually reads.

Every card on that page was reading zero, and zero is the one answer that looks the same
whether the work is done or the data is absent. Reading the derivations rather than guessing
at them turned up three things worth knowing, because each one means a table you would
naturally fill does not reach the page at all:

* **PPM health comes from work_orders, not from ppm_visits.** The docstring says so outright:
  "Derived from the work orders themselves rather than from a compliance table, because there
  isn't one." A planned order that is finished counts as done; one past its due date and not
  finished is missed; one finished after its due date is late. This database already held 264
  ppm_visits and the table still read "0 of 0 visits", because none of it was a work order.
  Contracts are groups of (vendor, work-order type) — not rows in vendor_contracts.

* **Decisions need a state, and state comes from status plus a due date.** Blocked is a status
  word; Deviation is a live order whose sla_due_at has passed. No work order here carried an
  sla_due_at, so nothing could ever deviate, and no status matched the blocked or awaiting
  spellings. Five decisions were showing, all of them from approvals_queue_items, which is the
  "To raise" source — an order somebody says should exist and does not.

* **Inspection intelligence counts reports by asset.** Sixteen inspections were on record with
  a recommendation each, and the card read "0 reports · 0 assets" because asset_id was null on
  every row. A finding nobody can attach to a thing is not a finding anyone can act on.

The numbers below are chosen so each card says something a reader can act on rather than to
make the page green. One contract behind plan, one watched, one to plan; a blocked vendor
holding up statutory gas work; recommendations that were never converted. A page where
everything is fine teaches nobody what the page is for.

Defaults to a dry run. Refuses to touch plenum_agent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import date, datetime, timedelta

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")
PRODUCTION = "plenum_agent"

#: A contract is a (vendor, work-order type) pair as far as the PPM table is concerned.
#: planned / done / missed / late are what the state rule reads: three or more missed, or
#: under 80 per cent complete, is behind plan; any missed or late, or under 100 per cent, is
#: watch; otherwise to plan. One of each, so the thresholds are visible on the page.
CONTRACTS = [
    ("Heating and gas", "Meridian Heating Ltd", "Boilers, DHW, gas safety",
     18, 12, 4, 2, ("boiler", "pump")),
    ("Electrical", "Northgate Electrical Ltd", "EICR, emergency lighting",
     14, 12, 1, 1, ("lighting", "door", "fire")),
    ("Mechanical PPM", "Apex Mechanical Ltd", "AHUs, chillers, pumps, FCUs",
     32, 29, 1, 2, ("chiller", "ahu", "fcu", "crac")),
]

#: The decisions a person owes, beyond the ones approvals_queue_items already raises.
#: (status, how many, how far past due, why)
#:
#: These carry NO wo_type. The PPM table groups by (vendor, wo_type) and counts anything with
#: a type as a planned visit, so a corrective order with one showed up as a fourth contract —
#: "Corrective · 0 of 6 · behind plan" — which is a contract nobody has and a failure nobody
#: owns. Corrective work is a decision, not a plan.
DECISIONS = [
    ("Blocked", 2, None,
     "Vendor blocked · Gas Safe registration lapsed. Statutory work cannot be allocated."),
    ("pending_approval", 2, None,
     "Drafted and waiting for an approver."),
    ("Open", 2, 9,
     "Live and past its SLA date."),
]


def dsn_for() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


async def vendor_id(c, org, name: str) -> uuid.UUID:
    """The vendor by that name, created if this database has not got one."""
    got = await c.fetchval(
        "SELECT id FROM plenum_cafm.vendors WHERE lower(vendor_name) = lower($1) LIMIT 1", name)
    if got:
        return got
    vid = uuid.uuid5(NS, f"vendor:{name}")
    await c.execute("""
        INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, status, country)
        VALUES ($1, $2, $3, 'active', 'United Kingdom')
        ON CONFLICT (id) DO NOTHING""", vid, org, name)
    return vid


async def run(db: str, apply: bool) -> None:
    if db == PRODUCTION:
        raise SystemExit(f"refusing to write to {PRODUCTION}. This is invented maintenance "
                         f"history; it belongs in a test database.")
    c = await asyncpg.connect(dsn_for(), database=db, timeout=90)
    print(f"\n################ {db} ({'APPLY' if apply else 'dry run'}) ################\n")

    buildings = await c.fetch("""
        SELECT building_id, building_code, name, organization_id
          FROM plenum_cafm.buildings ORDER BY building_code""")
    if not buildings:
        raise SystemExit("no buildings on record")
    org = buildings[0]["organization_id"]
    today = date.today()
    now = datetime.utcnow()

    assets = await c.fetch("""
        SELECT id, asset_code, asset_name, building_id FROM plenum_cafm.assets
         WHERE building_id IS NOT NULL ORDER BY asset_code""")
    print(f"  buildings {len(buildings)}   assets {len(assets)}")

    def assets_like(words) -> list:
        hit = [a for a in assets
               if any(w in (a["asset_name"] or "").lower() for w in words)]
        return hit or list(assets)

    # ── PPM work orders: what the contract table is actually counting ────────────────────
    planned = done = missed = late = 0
    for label, vname, scope, n_plan, n_done, n_missed, n_late, words in CONTRACTS:
        vid = await vendor_id(c, org, vname) if apply else uuid.uuid5(NS, f"vendor:{vname}")
        pool = assets_like(words)
        for i in range(n_plan):
            a = pool[i % len(pool)]
            due = today - timedelta(days=int((n_plan - i) * 340 / max(n_plan, 1)))
            wid = uuid.uuid5(NS, f"ppm:{label}:{i}")
            if i < n_done:
                status, closed = "Completed", due + timedelta(days=2 if i < n_late else 0)
            elif i < n_done + n_missed:
                status, closed = "Open", None          # past due and not finished = missed
            else:
                status, closed = "Open", None
                due = today + timedelta(days=14 + i)   # still to come
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.work_orders
                        (id, organization_id, building_id, asset_id, title, wo_type,
                         maintenance_type, status, priority, assigned_vendor, vendor_id,
                         sla_due_at, closed_at, completed_at, estimated_cost, conflict_flag)
                    VALUES ($1, $2, $3, $4, $5, $6, 'preventive', $7, 'medium', $8, $8,
                            $9, $10, $11, $12, false)
                    ON CONFLICT (id) DO UPDATE
                       SET status = EXCLUDED.status, sla_due_at = EXCLUDED.sla_due_at,
                           closed_at = EXCLUDED.closed_at, completed_at = EXCLUDED.completed_at,
                           wo_type = EXCLUDED.wo_type, assigned_vendor = EXCLUDED.assigned_vendor""",
                    wid, org, a["building_id"], a["id"],
                    f"{label} · {a['asset_name']}", label, status, vid,
                    datetime.combine(due, datetime.min.time()),
                    datetime.combine(closed, datetime.min.time()) if closed else None,
                    datetime.combine(closed, datetime.min.time()) if closed else None,
                    round(380 + (i * 37) % 900, 2))
            planned += 1
            done += 1 if status == "Completed" else 0
        missed += n_missed
        late += n_late
        pct = round(100.0 * n_done / n_plan, 1)
        state = ("behind plan" if (n_missed >= 3 or pct < 80.0)
                 else "watch" if (n_missed or n_late or pct < 100.0) else "to plan")
        print(f"  {label:18} {vname:26} {n_done}/{n_plan} done · {n_missed} missed · "
              f"{n_late} late → {state}")

    # ── The decisions a person owes ──────────────────────────────────────────────────────
    print()
    for status, count, overdue_days, why in DECISIONS:
        for i in range(count):
            a = assets[(i * 7) % len(assets)]
            wid = uuid.uuid5(NS, f"decision:{status}:{i}")
            due = (now - timedelta(days=overdue_days)) if overdue_days else (now + timedelta(days=21))
            if apply:
                vid = await vendor_id(c, org, "Meridian Heating Ltd")
                await c.execute("""
                    INSERT INTO plenum_cafm.work_orders
                        (id, organization_id, building_id, asset_id, title, wo_type,
                         maintenance_type, status, priority, assigned_vendor, vendor_id,
                         sla_due_at, estimated_cost, description, conflict_flag)
                    VALUES ($1, $2, $3, $4, $5, NULL, 'corrective', $6, 'high', $7, $7,
                            $8, $9, $10, false)
                    ON CONFLICT (id) DO UPDATE
                       SET status = EXCLUDED.status, sla_due_at = EXCLUDED.sla_due_at,
                           wo_type = NULL, description = EXCLUDED.description""",
                    wid, org, a["building_id"], a["id"],
                    f"{a['asset_name']} · {status}", status, vid, due,
                    round(320 + i * 260, 2), why)
        shown = {"Blocked": "Blocked", "pending_approval": "Awaiting approval",
                 "Open": "Deviation"}[status]
        print(f"  {shown:20} {count}   {why}")

    # ── Findings that name the thing they are about ──────────────────────────────────────
    linked = 0
    if apply and assets:
        rows = await c.fetch("SELECT id FROM plenum_cafm.inspections WHERE asset_id IS NULL")
        for i, r in enumerate(rows):
            a = assets[i % len(assets)]
            await c.execute("""
                UPDATE plenum_cafm.inspections
                   SET asset_id = $2, asset_code = $3 WHERE id = $1""",
                r["id"], a["id"], a["asset_code"])
            linked += 1
    print(f"\n  inspections given an asset: {linked}")

    if apply:
        after = await c.fetchrow("""
            SELECT count(*) FILTER (WHERE lower(status) IN ('blocked','on hold','held')) blocked,
                   count(*) FILTER (WHERE lower(status) IN
                        ('pending_approval','pending approval','awaiting approval','submitted','draft')) awaiting,
                   count(*) FILTER (WHERE lower(status) IN
                        ('open','in progress','in_progress','assigned','scheduled')
                        AND sla_due_at IS NOT NULL AND sla_due_at < now()) deviating,
                   count(*) FILTER (WHERE wo_type IS NOT NULL) planned
              FROM plenum_cafm.work_orders""")
        recs = await c.fetchval("""
            SELECT count(*) FROM plenum_cafm.inspections
             WHERE recommendation IS NOT NULL AND converted_work_order_id IS NULL""")
        report_assets = await c.fetchval(
            "SELECT count(DISTINCT asset_id) FROM plenum_cafm.inspections WHERE asset_id IS NOT NULL")
        print(f"\n  the cards should now read:")
        print(f"    Decisions owed          {after['blocked'] + after['awaiting'] + after['deviating']} "
              f"from work orders (+ whatever approvals raises as 'to raise')")
        print(f"      {after['blocked']} blocked · {after['deviating']} deviating · "
              f"{after['awaiting']} awaiting approval")
        print(f"    Recommendations unconv. {recs}")
        print(f"    Inspection reports      across {report_assets} assets")
        print(f"    PPM to plan             {done} of {planned} visits · {missed} missed")
    else:
        print("\n  dry run. Nothing was written. Add --apply.")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the Northbridge maintenance history.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
