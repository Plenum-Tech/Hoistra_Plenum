"""One building, recorded twice: fold the unowned copy into the owned one.

Three buildings exist as two rows each in ``plenum_cafm.sites``. One row carries the survey
— floors, floor area, use type, hoist score, city — and belongs to no organisation and has
no row in ``plenum_cafm.buildings``. The other belongs to TechCorp, has its building, has
the certificates filed against it, and carries none of the survey.

    B-001  Bishopsgate Tower   UK  Commercial  38,276 m²  34 floors  hoist 88   ← the survey
    S-01   Bishopsgate Tower   TechCorp, has the building, has the certificate  ← the owner

The Buildings page roots on the graph and reads the owned row, so it shows a building with
no area, no floors and no score while the figures sit one row away under a key nobody
references. Nothing references the unowned rows at all — not a building, not a certificate,
not a meter, not a work order, not a country pack.

What this does, and what it refuses to do:

  * **Pairs on the name, and only when the pairing is unambiguous.** Exactly one unowned
    row and exactly one owned row bearing that name, or the name is reported and skipped.
  * **Refuses a pair that disagrees about where it is.** Two rows naming different countries
    are two buildings with the same name, not one building twice.
  * **Only ever fills a NULL.** The owned row is the survivor and anything it already states
    it keeps — including where the two disagree. Disagreements are printed rather than
    resolved: ``S-01`` says its region is "South East" and ``B-001`` says "Greater London",
    and picking one of those is a decision about a building, not a merge.
  * **Deletes the absorbed row only when nothing references it**, re-checked inside the
    transaction immediately before the delete. Left in place, it would be recreated as a
    duplicate building the next time ``building_backfill.py`` runs.
  * **Leaves the unowned rows that have no twin alone.** Kingsway House, Meridian Quay,
    Riverside Court, Marina Heights, Northgate Mall and Raffles Link are not duplicates of
    anything — they are buildings with no owner, which is a different question.

Dry run by default. ``--apply`` writes, in one transaction.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

SCHEMA = "plenum_cafm"

#: Columns carried from the absorbed row to the survivor, where the survivor holds NULL.
#: The identity columns are deliberately absent — site_id, organization_id, site_name and
#: building_code. The survivor keeps its own key, its own owner and its own code, which is
#: the whole point of it being the survivor; stamping the absorbed row's building_code onto
#: it would leave sites.building_code naming one building and the buildings row naming
#: another, which is the confusion this merge exists to remove.
CARRY: tuple[str, ...] = (
    "site_type", "use_type", "use_mix", "floors", "gfa_sqm", "city", "postcode",
    "address", "country", "country_code", "state", "region", "timezone",
    "occupancy_profile", "hoist_score", "eui_kwh_per_m2", "benchmark_kwh_per_m2",
    "benchmark_standard", "benchmark_standing", "benchmark_standing_note",
    "metering_route", "metering_granularity",
)

#: Every place a site key is referenced. An absorbed row is removed only if all are zero.
REFERENCES: tuple[tuple[str, str], ...] = (
    ("buildings", "site_id"),
    ("compliance_certificates", "site_ref"),
    ("building_country_packs", "site_id"),
    ("energy_meters", "site_id"),
    ("work_orders", "site_id"),
    ("eui_snapshots", "site_id"),
    ("building_energy_profiles", "site_id"),
)


async def columns(c: asyncpg.Connection, table: str) -> set[str]:
    rows = await c.fetch(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2""", SCHEMA, table)
    return {r["column_name"] for r in rows}


async def references(c: asyncpg.Connection, site_id: str) -> dict[str, int]:
    """How many rows anywhere still name this site key."""
    found: dict[str, int] = {}
    for table, column in REFERENCES:
        cols = await columns(c, table)
        if not cols or column not in cols:
            continue
        try:
            n = await c.fetchval(
                f'SELECT count(*) FROM {SCHEMA}."{table}" WHERE "{column}"::text = $1', site_id)
        except Exception:  # noqa: BLE001 — a shape this deployment does not have
            continue
        if n:
            found[f"{table}.{column}"] = n
    return found


async def pairs(c: asyncpg.Connection) -> tuple[list[dict], list[str]]:
    """(unambiguous pairs, names that could not be paired)."""
    rows = await c.fetch(f"""
        SELECT site_id, site_name, organization_id::text AS org, country_code,
               (EXISTS (SELECT 1 FROM {SCHEMA}.buildings b WHERE b.site_id = s.site_id))
                   AS has_building
          FROM {SCHEMA}.sites s
         WHERE nullif(trim(site_name), '') IS NOT NULL
    """)
    by_name: dict[str, list[asyncpg.Record]] = {}
    for r in rows:
        by_name.setdefault(r["site_name"].strip().lower(), []).append(r)

    out, ambiguous = [], []
    for name, group in sorted(by_name.items()):
        if len(group) < 2:
            continue
        owners = [r for r in group if r["org"] and r["has_building"]]
        absorbed = [r for r in group if not r["org"] and not r["has_building"]]
        if len(owners) != 1 or len(absorbed) != 1 or len(group) != 2:
            ambiguous.append(
                f"{group[0]['site_name']}: {len(group)} rows, {len(owners)} owned with a "
                f"building, {len(absorbed)} unowned without one — left alone")
            continue
        owner, other = owners[0], absorbed[0]
        oc, ac = owner["country_code"], other["country_code"]
        if oc and ac and str(oc).upper() != str(ac).upper():
            ambiguous.append(
                f"{owner['site_name']}: {owner['site_id']} is in {oc} and {other['site_id']} "
                f"is in {ac} — two buildings with one name, not one building twice")
            continue
        out.append({"name": owner["site_name"], "survivor": owner["site_id"],
                    "absorbed": other["site_id"]})
    return out, ambiguous


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        await c.close()
        return 2
    print(f"database: {db}\n")

    have = await columns(c, "sites")
    carry = [col for col in CARRY if col in have]
    found, ambiguous = await pairs(c)

    plans = []
    for p in found:
        survivor = await c.fetchrow(
            f'SELECT * FROM {SCHEMA}.sites WHERE site_id = $1', p["survivor"])
        absorbed = await c.fetchrow(
            f'SELECT * FROM {SCHEMA}.sites WHERE site_id = $1', p["absorbed"])
        fills = {col: absorbed[col] for col in carry
                 if survivor[col] in (None, "") and absorbed[col] not in (None, "")}
        disagree = {col: (survivor[col], absorbed[col]) for col in carry
                    if survivor[col] not in (None, "") and absorbed[col] not in (None, "")
                    and str(survivor[col]).strip() != str(absorbed[col]).strip()}
        refs = await references(c, p["absorbed"])
        plans.append({**p, "fills": fills, "disagree": disagree, "refs": refs})

    for p in plans:
        print(f"  {p['name']}  —  {p['absorbed']} folded into {p['survivor']}")
        for col, v in p["fills"].items():
            print(f"      + {col:<24} {v}")
        for col, (keep, drop) in p["disagree"].items():
            print(f"      = {col:<24} keeping {keep!r} (the absorbed row says {drop!r})")
        print(f"      {('references remain: ' + str(p['refs'])) if p['refs'] else
                      'nothing references ' + p['absorbed'] + ' — it will be removed'}")
    for a in ambiguous:
        print(f"  skipped — {a}")
    print(f"\n  {len(plans)} pairs, {len(ambiguous)} left alone")

    if not apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        merged, removed = 0, 0
        for p in plans:
            if p["fills"]:
                params = [p["survivor"]]
                sets = []
                for col, v in p["fills"].items():
                    params.append(v)
                    # coalesce, not assignment: the survivor keeps anything it already states
                    # even if this ran twice, or if another process filled it meanwhile.
                    sets.append(f'"{col}" = coalesce("{col}", ${len(params)})')
                await c.execute(
                    f'UPDATE {SCHEMA}.sites SET {", ".join(sets)} WHERE site_id = $1', *params)
                merged += 1
            # Re-checked here, not trusted from the plan: the reference count that matters
            # is the one true at the moment of the delete.
            refs = await references(c, p["absorbed"])
            if refs:
                print(f"  keeping {p['absorbed']} — {refs} still name it")
                continue
            await c.execute(f'DELETE FROM {SCHEMA}.sites WHERE site_id = $1', p["absorbed"])
            removed += 1

        # Every survivor must now hold what it was given, and no absorbed key may survive
        # with references — otherwise the merge left the data in neither row.
        for p in plans:
            row = await c.fetchrow(
                f'SELECT * FROM {SCHEMA}.sites WHERE site_id = $1', p["survivor"])
            missing = [col for col in p["fills"] if row[col] in (None, "")]
            if missing:
                await tr.rollback()
                print(f"ROLLED BACK — {p['survivor']} did not take {missing}")
                await c.close()
                return 1
        await tr.commit()
    except Exception as exc:  # noqa: BLE001
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    print(f"  {merged} rows took the survey from their duplicate, {removed} duplicates removed")
    for p in plans:
        row = await c.fetchrow(
            f"""SELECT site_id, site_name, country_code, site_type, floors, gfa_sqm, hoist_score, city
                  FROM {SCHEMA}.sites WHERE site_id = $1""", p["survivor"])
        print("   ", dict(row))
    await c.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", default="plenum_agent")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dsn = a.dsn
    if not dsn:
        raw = next(l.split("=", 1)[1].strip() for l in
                   open(os.path.join("apps", "backend", ".env"), encoding="utf-8")
                   if l.startswith("DB_URL="))
        dsn = re.sub("^postgresql[+]asyncpg://", "postgresql://", raw)
    dsn = re.sub(r"/[^/?]+(\?|$)", f"/{a.database}\\1", dsn)
    return asyncio.run(run(dsn, a.database, a.apply))


if __name__ == "__main__":
    sys.exit(main())
