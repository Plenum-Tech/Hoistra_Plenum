"""Fill sites.region from the column that already holds the region.

`region` is null on every site in the test portfolio and on 616 of 626 in production, while
`state` carries exactly the fact region is meant to hold — Greater London, Greater
Manchester, Scotland, Dubai, New York, Central Region — and `city` carries it for the
city-states where the two are the same thing.

So nothing is derived, inferred or invented here: one column is copied into another that
means the same thing. Where neither source has a value the row is left alone, because an
empty region is a true statement about a site nobody has recorded a region for, and a
guessed one is not.

`shape_building_row()` already falls back `region → state → city` when it builds a row, so
the reads work either way. This makes the stored column agree with what the reads compute,
which is what anything querying `sites.region` directly — a filter, an export, a report —
actually needs.

    python db/tools/backfill_site_region.py --to <dsn>           # dry run
    python db/tools/backfill_site_region.py --to <dsn> --apply

Dry run by default. --apply is the only thing that writes, and it only ever fills nulls:
a region somebody has already recorded is never overwritten.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

import asyncpg

#: In preference order. `state` is the administrative region; `city` stands in for the
#: city-states and single-city territories where the two genuinely coincide.
SOURCES = ("state", "city")


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", required=True, help="Postgres DSN")
    ap.add_argument("--apply", action="store_true", help="Write. Without it, nothing changes.")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.to, ssl=_ssl_for(args.to))
    try:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'plenum_cafm' AND table_name = 'sites'"
            )
        }
        if "region" not in cols:
            print("sites.region does not exist in this database — nothing to fill.")
            return
        usable = [c for c in SOURCES if c in cols]
        if not usable:
            print(f"none of {SOURCES} exist on sites — nothing to derive from.")
            return

        # COALESCE over whichever sources this database has, in order.
        expr = "COALESCE(" + ", ".join(f"NULLIF(btrim({c}), '')" for c in usable) + ")"
        total = await conn.fetchval("SELECT count(*) FROM plenum_cafm.sites")
        filled = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.sites WHERE NULLIF(btrim(region), '') IS NOT NULL"
        )
        rows = await conn.fetch(
            f"""SELECT site_id, site_name, {expr} AS derived
                  FROM plenum_cafm.sites
                 WHERE NULLIF(btrim(region), '') IS NULL
                   AND {expr} IS NOT NULL
                 ORDER BY site_id"""
        )
        unfillable = await conn.fetchval(
            f"""SELECT count(*) FROM plenum_cafm.sites
                 WHERE NULLIF(btrim(region), '') IS NULL AND {expr} IS NULL"""
        )

        print(f"sites: {total} rows — {filled} already have a region, {len(rows)} can be "
              f"filled from {'/'.join(usable)}, {unfillable} have no source and stay null.\n")
        for r in rows[:40]:
            print(f"  {str(r['site_id'] or ''):<10} {str(r['site_name'] or '')[:26]:<26} "
                  f"-> {r['derived']}")
        if len(rows) > 40:
            print(f"  … and {len(rows) - 40} more")

        print()
        if not args.apply:
            print(f"dry run — nothing written. --apply would fill {len(rows)} row(s).")
            return

        tag = await conn.execute(
            f"""UPDATE plenum_cafm.sites
                   SET region = {expr}
                 WHERE NULLIF(btrim(region), '') IS NULL
                   AND {expr} IS NOT NULL"""
        )
        after = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.sites WHERE NULLIF(btrim(region), '') IS NOT NULL"
        )
        print(f"{tag}. region populated on {after} of {total} sites (was {filled}).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
