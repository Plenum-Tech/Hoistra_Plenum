"""Give the Feature B demo rows the company they already belong to.

``scripts/generate_feature_b_6m_data.py`` stamps everything it produces with two ids it
invents and never creates::

    ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
    PM_ID  = uuid.UUID("00000000-0000-0000-0000-0000000000f1")

So the six months of synthetic contract-performance data seeded onto this deployment names
a company that is not in ``organizations`` and a PM who is not in ``users``. Scoped reads
filter on the company, and a company that does not exist matches nobody: the rows are
present, they are counted by nothing, and they belong to no one.

Where they actually belong is not a guess. Every one of those rows that names a vendor
names one of the dataset's own three synthetic vendors — ``…00a1``, ``…00a2``, ``…00a3`` —
and all three already sit under **Feature B Demo Org** (``…0005``). The vendors were moved
at some point and the rows that refer to them were not. This finishes that move.

Two things are deliberately not done.

``ops_audit_log`` is append-only — it has triggers forbidding both UPDATE and DELETE — so
its rows keep the id they were written with. An audit line records what happened at the
time, including the fact that the seed named a company that did not exist; rewriting it
would be the one edit this table exists to prevent.

Nothing is deleted and nothing is invented. The PM is created as a real user row in the
same demo company, because twelve asset-criticality approvals record that this person
approved them; nulling the reference would throw away who approved what in order to make a
foreign key resolve.

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

#: The id the generator mints, and the company its own vendors already live under.
SEEDED_ORG = "00000000-0000-0000-0000-0000000000b1"
REAL_ORG = "00000000-0000-0000-0000-000000000005"

#: The PM the generator acts as. Created, not nulled — see the module docstring.
SEEDED_PM = "00000000-0000-0000-0000-0000000000f1"
PM_EMAIL = "feature-b-demo-pm@plenum-tech.invalid"
PM_NAME = "Feature B demo PM (synthetic)"

#: Append-only: both UPDATE and DELETE are refused by a trigger, by design.
LEAVE_ALONE = {"ops_audit_log"}


async def stamped(c: asyncpg.Connection) -> dict[str, int]:
    """Every table holding a row stamped with the seeded company, and how many."""
    out: dict[str, int] = {}
    rows = await c.fetch(
        """SELECT c.table_name FROM information_schema.columns c
             JOIN information_schema.tables t
               ON t.table_schema = c.table_schema AND t.table_name = c.table_name
            WHERE c.table_schema = $1 AND c.column_name = 'organization_id'
              AND c.data_type = 'uuid' AND t.table_type = 'BASE TABLE'
            ORDER BY c.table_name""",
        SCHEMA,
    )
    for r in rows:
        table = r["table_name"]
        try:
            n = await c.fetchval(
                f'SELECT count(*) FROM {SCHEMA}."{table}" WHERE organization_id = $1::uuid',
                SEEDED_ORG,
            )
        except Exception:  # noqa: BLE001 — a table this deployment does not have
            continue
        if n:
            out[table] = n
    return out


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        await c.close()
        return 2
    print(f"database: {db}")

    target = await c.fetchrow(
        f"SELECT id::text, name FROM {SCHEMA}.organizations WHERE id = $1::uuid", REAL_ORG)
    if not target:
        print(f"refusing: {REAL_ORG} is not an organisation on this deployment")
        await c.close()
        return 2
    print(f"target company: {target['name']} ({target['id']})")

    before = await stamped(c)
    movable = {t: n for t, n in before.items() if t not in LEAVE_ALONE}
    print(f"\n{'table':<34} {'rows':>7}")
    for t, n in sorted(before.items()):
        note = "  (append-only — left as written)" if t in LEAVE_ALONE else ""
        print(f"  {t:<32} {n:>7}{note}")
    print(f"\n  to restamp: {sum(movable.values())} rows across {len(movable)} tables")

    pm = await c.fetchrow(f"SELECT id::text FROM {SCHEMA}.users WHERE id = $1::uuid", SEEDED_PM)
    approvals = await c.fetchval(
        f"SELECT count(*) FROM {SCHEMA}.asset_criticality WHERE approved_by = $1::uuid", SEEDED_PM)
    print(f"  demo PM on record: {'yes' if pm else 'no'} — {approvals} approvals name them")

    if not apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        if not pm:
            await c.execute(
                f"""INSERT INTO {SCHEMA}.users (id, organization_id, email, full_name,
                                                platform_role, status, email_verified)
                    VALUES ($1::uuid, $2::uuid, $3, $4, 'user', 'active', false)
                    ON CONFLICT (id) DO NOTHING""",
                SEEDED_PM, REAL_ORG, PM_EMAIL, PM_NAME)
            print(f"  created the demo PM as a user of {target['name']}")

        moved: list[tuple[str, int]] = []
        refused: dict[str, str] = {}
        for table in sorted(movable):
            # Per table, in its own subtransaction: a unique or check constraint that one
            # table trips must not roll back the tables that moved cleanly.
            try:
                async with c.transaction():
                    result = await c.execute(
                        f'UPDATE {SCHEMA}."{table}" SET organization_id = $1::uuid '
                        f"WHERE organization_id = $2::uuid",
                        REAL_ORG, SEEDED_ORG)
                moved.append((table, int(result.split()[-1])))
            except Exception as exc:  # noqa: BLE001
                refused[table] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:80]}"

        after = await stamped(c)
        left = {t: n for t, n in after.items() if t not in LEAVE_ALONE}
        if left and not refused:
            await tr.rollback()
            print(f"ROLLED BACK — rows still stamped with the seeded id: {left}")
            await c.close()
            return 1
        await tr.commit()
    except Exception as exc:  # noqa: BLE001
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    for table, n in moved:
        print(f"  {table:<32} {n:>6} rows now belong to {target['name']}")
    for table, why in refused.items():
        print(f"  {table:<32} REFUSED — {why}")

    after = await stamped(c)
    print(f"\nstill stamped with the seeded id: "
          f"{after or 'nothing'}")
    print("  (ops_audit_log keeps what it was written with — it is append-only)")
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
