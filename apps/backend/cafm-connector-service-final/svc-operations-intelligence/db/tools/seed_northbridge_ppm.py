"""The PPM health table: contracts, and visits that can reach a building.

There are two PPM functions in the maintenance service and they do not read the same thing.
`ppm_health` derives contracts from work_orders grouped by vendor. `ppm_health_by_contract` —
the one the page calls, at /api/maintenance/ppm/contracts — reads `ppm_visits`, keyed on the
contract rather than the vendor, because one vendor can hold several and rolling them together
hides the one that is behind.

That table read "0 of 0 planned visits" over 264 visits on record. Its query is:

    FROM plenum_cafm.ppm_visits v
    JOIN plenum_cafm.assets a ON a.id::text = v.asset_id::text
    WHERE a.building_id IS NOT NULL AND v.scheduled_date >= <1 Jan this year>

An INNER join. Every one of those 264 visits had a null asset_id, so every one was dropped
before anything was counted — and a visit that cannot name the thing it visited cannot be
attributed to a building either, so there was no honest way to show it. None carried a
contract_id either, and vendor_contracts was empty, so even the ones that survived would have
grouped under "Unassigned".

This replaces them with visits that can be reached. What it writes and why each column earns
its place:

    asset_id        the inner join; without it the visit does not exist to this page
    contract_id     the grouping key; without it every row collapses into "Unassigned"
    vendor_id       the name shown when a contract has none
    scheduled_date  the year-to-date window, and what "missed" is judged against
    completed_date  done; and late is completed beyond scheduled + tolerance_days
    tolerance_days  a visit booked for the 1st and done on the 3rd inside a 7-day window was
                    on time. Counting it late makes every contract look worse than it is
    deferred        agreed to move, which is not the same fact as nobody turned up
    inspection_id   what "reports on file" counts

Five contracts: one behind plan, three watched, one to plan, so the thresholds on the page are
visible rather than asserted. Every figure is invented.

Defaults to a dry run. Refuses to touch plenum_agent.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import uuid
from datetime import date, timedelta

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
NS = uuid.UUID("6f1d3a52-9c47-4f8e-9b2a-7d5e1c084a33")
PRODUCTION = "plenum_agent"

#: contract, vendor, scope, planned, done, missed, late, deferred, reports, asset words
#: Behind plan is three or more missed or under 80 per cent; watch is any missed, any late, or
#: under 100 per cent; to plan otherwise. One of each kind, so the rule is legible on the page.
CONTRACTS = [
    ("Heating and gas · UK", "Meridian Heating Ltd", "Boilers, DHW, gas safety",
     18, 12, 4, 2, 2, 10, ("boiler", "pump")),
    ("Electrical · UK", "Northgate Electrical Ltd", "EICR, emergency lighting",
     14, 12, 1, 1, 1, 11, ("generator", "lighting")),
    ("Mechanical PPM · UK", "Apex Mechanical Ltd", "AHUs, chillers, pumps, FCUs",
     32, 29, 1, 2, 1, 27, ("chiller", "ahu", "fcu", "crac")),
    ("Lifts · UK", "Apex Lift Engineering Ltd", "LOLER, lift maintenance",
     12, 11, 0, 1, 0, 11, ("lift",)),
    ("Fire and security · UK", "Sentinel Fire and Security Ltd", "Alarms, access control",
     12, 12, 0, 0, 0, 12, ("fire", "door")),
]
TOLERANCE_DAYS = 7


def dsn_for() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


async def vendor_id(c, org, name: str):
    got = await c.fetchval(
        "SELECT id FROM plenum_cafm.vendors WHERE lower(vendor_name) = lower($1) LIMIT 1", name)
    if got:
        return got
    vid = uuid.uuid5(NS, f"vendor:{name}")
    await c.execute("""
        INSERT INTO plenum_cafm.vendors (id, organization_id, vendor_name, status, country)
        VALUES ($1, $2, $3, 'active', 'United Kingdom') ON CONFLICT (id) DO NOTHING""",
        vid, org, name)
    return vid


async def run(db: str, apply: bool) -> None:
    if db == PRODUCTION:
        raise SystemExit(f"refusing to write to {PRODUCTION}. This is invented visit history.")
    c = await asyncpg.connect(dsn_for(), database=db, timeout=90)
    print(f"\n################ {db} ({'APPLY' if apply else 'dry run'}) ################\n")

    org = await c.fetchval("SELECT organization_id FROM plenum_cafm.buildings LIMIT 1")
    assets = await c.fetch("""
        SELECT id, asset_code, asset_name FROM plenum_cafm.assets
         WHERE building_id IS NOT NULL ORDER BY asset_code""")
    if not assets:
        raise SystemExit("no assets with a building; seed the register first")

    orphans = await c.fetchval(
        "SELECT count(*) FROM plenum_cafm.ppm_visits WHERE asset_id IS NULL")
    print(f"  assets {len(assets)}   visits that name no asset: {orphans}")
    print(f"  (those cannot reach the page at all — the query inner-joins assets)\n")

    today = date.today()
    jan = date(today.year, 1, 1)
    span = max((today - jan).days, 60)

    tot = {"planned": 0, "done": 0, "missed": 0, "late": 0, "deferred": 0, "reports": 0}
    for name, vname, scope, n_plan, n_done, n_missed, n_late, n_def, n_rep, words in CONTRACTS:
        pool = [a for a in assets
                if any(w in (a["asset_name"] or "").lower() for w in words)] or list(assets)
        cid = uuid.uuid5(NS, f"contract:{name}")
        if apply:
            vid = await vendor_id(c, org, vname)
            await c.execute("""
                INSERT INTO plenum_cafm.vendor_contracts
                    (id, organization_id, vendor_id, contract_name, contract_start,
                     contract_end, contract_value, service_scope, visits_per_year,
                     country_code, status, sla_terms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'GB', 'active', $10)
                ON CONFLICT (id) DO UPDATE
                   SET contract_name = EXCLUDED.contract_name,
                       service_scope = EXCLUDED.service_scope,
                       visits_per_year = EXCLUDED.visits_per_year""",
                cid, org, vid, name, today - timedelta(days=300), today + timedelta(days=430),
                n_plan * 640, scope, n_plan,
                "P1 4h response / 24h fix; P2 next business day; P3 five business days.")
            # Replace this contract's visits rather than adding to them, so a second run does
            # not double the plan.
            await c.execute("DELETE FROM plenum_cafm.ppm_visits WHERE contract_id = $1", cid)

        for i in range(n_plan):
            a = pool[i % len(pool)]
            sched = jan + timedelta(days=int((i + 1) * span / (n_plan + 1)))
            completed = deferred = None
            if i < n_done:
                completed = sched + timedelta(days=TOLERANCE_DAYS + 3 if i < n_late else 1)
                status = "Completed"
            elif i < n_done + n_missed:
                status = "Missed"                      # past, not completed, not deferred
            elif i < n_done + n_missed + n_def:
                status, deferred = "Deferred", True
            else:
                status = "Planned"
                sched = today + timedelta(days=10 + i)  # still to come
            if apply:
                await c.execute("""
                    INSERT INTO plenum_cafm.ppm_visits
                        (id, organization_id, vendor_id, contract_id, asset_id, asset_code,
                         ppm_ref, frequency, scheduled_date, completed_date, tolerance_days,
                         status, source, deferred, inspection_id, vendor_name, raw_metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'quarterly', $8, $9, $10, $11,
                            'seed', $12, $13, $14, '{}'::jsonb)""",
                    uuid.uuid5(NS, f"visit:{name}:{i}"), org, vid, cid, a["id"], a["asset_code"],
                    f"PPM-{a['asset_code']}-{i:02d}", sched, completed, TOLERANCE_DAYS, status,
                    bool(deferred), (f"IR-{a['asset_code']}-{i:02d}" if i < n_rep else None),
                    vname)
        # Two visits still to come, on every contract. Without one the table renders "blocked"
        # in the Next column — which is what it says when a contract genuinely cannot be
        # booked, so a contract that is simply up to date reads as one that is stuck. These do
        # not move the percentage: the page divides by visits_per_year, the plan the contract
        # committed to, not by how many rows happen to exist.
        if apply:
            for k in range(2):
                a = pool[(n_plan + k) % len(pool)]
                await c.execute("""
                    INSERT INTO plenum_cafm.ppm_visits
                        (id, organization_id, vendor_id, contract_id, asset_id, asset_code,
                         ppm_ref, frequency, scheduled_date, completed_date, tolerance_days,
                         status, source, deferred, raw_metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'quarterly', $8, NULL, $9, 'Planned',
                            'seed', false, '{}'::jsonb)""",
                    uuid.uuid5(NS, f"visit:{name}:future:{k}"), org, vid, cid, a["id"],
                    a["asset_code"], f"PPM-{a['asset_code']}-F{k}",
                    today + timedelta(days=21 + k * 30), TOLERANCE_DAYS)

        pct = round(100.0 * n_done / n_plan, 1)
        state = ("behind plan" if (n_missed >= 3 or pct < 80.0)
                 else "watch" if (n_missed or n_late or pct < 100.0) else "to plan")
        print(f"  {name:24} {n_done}/{n_plan}  missed {n_missed}  late {n_late}  "
              f"deferred {n_def}  reports {n_rep}  {pct}%  {state}")
        for k, v in (("planned", n_plan), ("done", n_done), ("missed", n_missed),
                     ("late", n_late), ("deferred", n_def), ("reports", n_rep)):
            tot[k] += v

    pct = round(100.0 * tot["done"] / max(tot["planned"], 1))
    print(f"\n  year to date: {tot['done']} of {tot['planned']} visits · {tot['missed']} missed "
          f"· {tot['reports']} reports on file · {tot['deferred']} deferrals   ({pct}%)")

    if apply:
        reach = await c.fetchval("""
            SELECT count(*) FROM plenum_cafm.ppm_visits v
              JOIN plenum_cafm.assets a ON a.id::text = v.asset_id::text
             WHERE a.building_id IS NOT NULL AND v.scheduled_date >= $1""", jan)
        print(f"  visits that now reach the page: {reach}")
        if orphans:
            print(f"\n  note: {orphans} older visit(s) still name no asset. They cannot reach "
                  f"this page or any other; delete them when you are sure they are not needed.")
    else:
        print("\n  dry run. Nothing was written. Add --apply.")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the PPM contracts and visits.")
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
