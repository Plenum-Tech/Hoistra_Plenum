"""Give a row the company of the row it belongs to.

The identity migration made every ``organization_id`` a uuid that can resolve, but it could
not invent one where none was recorded. Some tables were written before the column meant
anything and hold NULL: the energy meters and their readings, the anomalies raised from
them, the EUI snapshots, the vendor scorecards. Scoped reads treat NULL as "belongs to
nobody", so those rows are invisible to every screen — the Energy page reads empty even
though the readings are there.

None of it needs guessing. A meter belongs to the company that owns the building it is on; a
reading belongs to its meter's company; a scorecard belongs to its vendor's. Each derivation
below names the row it inherits from, and the rule throughout is the same:

  * only ever fill a NULL — a company already recorded is never overwritten;
  * only ever from a row that has one — no default, no fallback to "the first company";
  * a value that resolves to no organisation (the ``…00b1`` demo placeholder and its
    friends) is left exactly as it is, because it is not NULL and not ours to reinterpret.

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

#: (what is being filled, the SQL that fills it, how to say it in English)
DERIVATIONS: list[tuple[str, str, str]] = [
    ("energy_meters", f"""
        UPDATE {SCHEMA}.energy_meters m SET organization_id = b.organization_id
          FROM {SCHEMA}.buildings b
         WHERE m.organization_id IS NULL AND b.organization_id IS NOT NULL
           AND b.building_id = m.site_id""",
     "the company that owns the building the meter is on"),

    ("meter_readings", f"""
        UPDATE {SCHEMA}.meter_readings r SET organization_id = m.organization_id
          FROM {SCHEMA}.energy_meters m
         WHERE r.organization_id IS NULL AND m.organization_id IS NOT NULL
           AND m.id = r.meter_id""",
     "its meter's company"),

    ("meter_reading_gaps", f"""
        UPDATE {SCHEMA}.meter_reading_gaps g SET organization_id = m.organization_id
          FROM {SCHEMA}.energy_meters m
         WHERE g.organization_id IS NULL AND m.organization_id IS NOT NULL
           AND m.id = g.meter_id""",
     "its meter's company"),

    ("energy_anomalies", f"""
        UPDATE {SCHEMA}.energy_anomalies a SET organization_id = m.organization_id
          FROM {SCHEMA}.energy_meters m
         WHERE a.organization_id IS NULL AND m.organization_id IS NOT NULL
           AND m.id = a.meter_id""",
     "the company of the meter it was detected on"),

    ("eui_snapshots", f"""
        UPDATE {SCHEMA}.eui_snapshots s SET organization_id = b.organization_id
          FROM {SCHEMA}.buildings b
         WHERE s.organization_id IS NULL AND b.organization_id IS NOT NULL
           AND b.building_id = s.site_id""",
     "the company that owns the building it measures"),

    ("building_energy_profiles", f"""
        UPDATE {SCHEMA}.building_energy_profiles p SET organization_id = b.organization_id
          FROM {SCHEMA}.buildings b
         WHERE p.organization_id IS NULL AND b.organization_id IS NOT NULL
           AND b.building_id = p.site_id""",
     "the company that owns the building it describes"),

    ("vendor_monthly_scorecards", f"""
        UPDATE {SCHEMA}.vendor_monthly_scorecards s SET organization_id = v.organization_id
          FROM {SCHEMA}.vendors v
         WHERE s.organization_id IS NULL AND v.organization_id IS NOT NULL
           AND v.id::text = s.vendor_id::text""",
     "the vendor's company"),

    ("vendor_wo_scores", f"""
        UPDATE {SCHEMA}.vendor_wo_scores s SET organization_id = v.organization_id
          FROM {SCHEMA}.vendors v
         WHERE s.organization_id IS NULL AND v.organization_id IS NOT NULL
           AND v.id::text = s.vendor_id::text""",
     "the vendor's company"),

    ("compliance_certificates", f"""
        UPDATE {SCHEMA}.compliance_certificates c SET organization_id = b.organization_id
          FROM {SCHEMA}.buildings b
         WHERE c.organization_id IS NULL AND c.org_id IS NULL
           AND b.organization_id IS NOT NULL AND b.building_id = c.building_id""",
     "the company that owns the building it is filed against"),

    ("compliance_risk_snapshots", f"""
        UPDATE {SCHEMA}.compliance_risk_snapshots s SET organization_id = b.organization_id
          FROM {SCHEMA}.buildings b
         WHERE s.organization_id IS NULL AND b.organization_id IS NOT NULL
           AND b.building_id = s.building_id""",
     "the company that owns the building it scores"),
]


async def counts(c: asyncpg.Connection) -> dict[str, tuple[int, int, int]]:
    """rows, rows with no company, rows whose company resolves — per table."""
    out: dict[str, tuple[int, int, int]] = {}
    for table, _, _ in DERIVATIONS:
        try:
            total = await c.fetchval(f'SELECT count(*) FROM {SCHEMA}."{table}"')
            nulls = await c.fetchval(
                f'SELECT count(*) FROM {SCHEMA}."{table}" WHERE organization_id IS NULL')
            real = await c.fetchval(f"""SELECT count(*) FROM {SCHEMA}."{table}" x
                                          JOIN {SCHEMA}.organizations o ON o.id = x.organization_id""")
            out[table] = (total, nulls, real)
        except Exception:  # noqa: BLE001 — a table this deployment does not have
            continue
    return out


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        return 2
    org_type = await c.fetchval("""SELECT data_type FROM information_schema.columns
        WHERE table_schema=$1 AND table_name='organizations' AND column_name='id'""", SCHEMA)
    if org_type != "uuid":
        print(f"refusing: organizations.id is {org_type} — run uuid_identity_migration.py first")
        return 2
    print(f"database: {db}")

    before = await counts(c)
    # A derivation is only attempted where every column it joins on exists. Deployments
    # differ — compliance_risk_snapshots has no building_id here — and one impossible join
    # inside the transaction would roll back every sound one with it.
    skipped: dict[str, str] = {}
    for table, sql, _ in DERIVATIONS:
        if table not in before:
            continue
        try:
            await c.execute(f"EXPLAIN {sql}")
        except Exception as exc:  # noqa: BLE001
            skipped[table] = str(exc).splitlines()[0][:90]
    print(f"\n{'table':<30} {'rows':>7} {'no company':>11} {'resolves':>9}")
    for t, (total, nulls, real) in before.items():
        print(f"  {t:<28} {total:>7} {nulls:>11} {real:>9}")

    for table, why in skipped.items():
        print(f"  {table:<28} skipped — {why}")
    if not apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        filled: list[tuple[str, int, str]] = []
        for table, sql, why in DERIVATIONS:
            if table not in before or table in skipped:
                continue
            result = await c.execute(sql)
            n = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            if n:
                filled.append((table, n, why))
        after = await counts(c)
        lost = {t: (before[t][0], after[t][0]) for t in before if before[t][0] != after[t][0]}
        regressed = {t: (before[t][2], after[t][2]) for t in before if after[t][2] < before[t][2]}
        if lost or regressed:
            await tr.rollback()
            print(f"ROLLED BACK — rows changed {lost}, resolution regressed {regressed}")
            await c.close()
            return 1
        await tr.commit()
    except Exception as exc:
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    for table, n, why in filled:
        print(f"  {table:<28} {n:>6} rows given {why}")
    after = await counts(c)
    print(f"\n{'table':<30} {'resolves before':>16} {'after':>7}")
    for t in before:
        print(f"  {t:<28} {before[t][2]:>16} {after[t][2]:>7}")
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
