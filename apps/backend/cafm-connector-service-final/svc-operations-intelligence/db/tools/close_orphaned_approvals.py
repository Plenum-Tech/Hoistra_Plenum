"""Close approvals that point at something no longer there.

An approvals item names what it is about by (related_entity_type, related_entity_id). Nothing
enforces that the thing still exists — it is a type name and a text id, not a foreign key — so
deleting the row it refers to leaves the decision behind, pending for ever, asking a person to
act on something that is gone.

Found on hoistra_test: 272 pending items, of which 83 resolved to a building and 161 pointed
at energy anomalies that had been deleted. The decisions engine excludes them correctly, so
the Maintenance page never showed them; but they sit in the queue, they are counted wherever a
raw pending count is taken, and nobody can ever clear one because there is nothing to open.

A decision whose subject has gone is not a decision anybody can make. This closes those, with
a reason recorded, and leaves every item whose subject still exists exactly as it was.

Reads only unless --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = r"C:\CMMS\Hoistra\.env"
PRODUCTION = "plenum_agent"

#: (related_entity_type, the table it names, the column its id matches)
SUBJECTS = [
    ("energy_anomaly", "energy_anomalies", "id"),
    ("compliance_certificate", "compliance_certificates", "id"),
    ("contract_sla_parameters", "contract_sla_parameters", "id"),
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


async def run(db: str, apply: bool) -> None:
    if db == PRODUCTION:
        raise SystemExit(
            f"refusing to write to {PRODUCTION}. Closing a decision is a decision; on "
            f"production it is one a person makes."
        )
    c = await asyncpg.connect(dsn_for(), database=db, timeout=60)
    print(f"\n################ {db} ({'APPLY' if apply else 'dry run'}) ################\n")

    total = await c.fetchval(
        "SELECT count(*) FROM plenum_cafm.approvals_queue_items WHERE status = 'pending'")
    print(f"  pending: {total}")

    closed = 0
    for kind, table, col in SUBJECTS:
        exists = await c.fetchval(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'plenum_cafm' AND table_name = $1", table)
        if not exists:
            print(f"  {kind:26} {table} is not on this database — left alone")
            continue
        orphans = await c.fetchval(f"""
            SELECT count(*) FROM plenum_cafm.approvals_queue_items q
             WHERE q.status = 'pending' AND q.related_entity_type = $1
               AND NOT EXISTS (SELECT 1 FROM plenum_cafm.{table} t
                                WHERE t.{col}::text = q.related_entity_id::text)""", kind)
        live = await c.fetchval("""
            SELECT count(*) FROM plenum_cafm.approvals_queue_items
             WHERE status = 'pending' AND related_entity_type = $1""", kind)
        print(f"  {kind:26} {live - orphans} live · {orphans} pointing at nothing")
        if apply and orphans:
            tag = await c.execute(f"""
                UPDATE plenum_cafm.approvals_queue_items q
                   SET status = 'closed',
                       decided_at = now(),
                       pm_notes = coalesce(q.pm_notes || ' ', '')
                           || 'Closed automatically: the ' || $1 || ' this decision was about '
                           || 'no longer exists, so there is nothing to act on.',
                       updated_at = now()
                 WHERE q.status = 'pending' AND q.related_entity_type = $1
                   AND NOT EXISTS (SELECT 1 FROM plenum_cafm.{table} t
                                    WHERE t.{col}::text = q.related_entity_id::text)""", kind)
            closed += int(tag.rsplit(" ", 1)[-1] or 0)

    unnamed = await c.fetchval("""
        SELECT count(*) FROM plenum_cafm.approvals_queue_items
         WHERE status = 'pending' AND coalesce(related_entity_type, '') = ''""")
    if unnamed:
        print(f"  {'(no subject named)':26} {unnamed} left alone — an item that names nothing "
              f"cannot be proved orphaned")

    if apply:
        after = await c.fetchval(
            "SELECT count(*) FROM plenum_cafm.approvals_queue_items WHERE status = 'pending'")
        print(f"\n  closed {closed}.  pending now {after}, was {total}")
    else:
        print("\n  dry run. Nothing was closed. Add --apply.")
    await c.close()


async def main() -> None:
    ap = argparse.ArgumentParser(description="Close approvals whose subject has gone.")
    ap.add_argument("--db", required=True, help="database name, e.g. hoistra_test")
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()
    await run(args.db, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
