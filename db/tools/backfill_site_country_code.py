"""Fill sites.country_code from the country the site already records.

country_code is set on 9 of 626 production sites while `country` is set on 612 — and every
distinct value it holds (UAE, United Kingdom, United States, Singapore) is one the engine
already resolves. So the code can be derived from data that is there, for almost the whole
portfolio, without anybody typing anything.

`country` is tried first because it is the column that means this. `region` is the fallback,
for the handful of rows with no country at all: after the region backfill it holds an
emirate or a home nation on most rows, and those name exactly one country. Where neither
resolves the row is left null — "South West" and "Central Region" name a part of a dozen
countries between them, and a guess there is worse than a gap.

The resolution is not reimplemented here. This imports country_code_for() from the engine,
so the tool and the thing that reads the column cannot drift apart — and so the guarantee
that an unmapped name yields None rather than itself holds in both.

    python db/tools/backfill_site_country_code.py --to <dsn>           # dry run
    python db/tools/backfill_site_country_code.py --to <dsn> --apply

Dry run by default. --apply only ever fills nulls; a code somebody recorded by hand is
never overwritten, and one that disagrees with the country beside it is reported rather
than quietly corrected — that is a data conflict for a person to settle, not a backfill.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

_ENGINE = (Path(__file__).resolve().parents[2] / "apps" / "backend"
           / "cafm-connector-service-final" / "svc-operations-intelligence")
sys.path.insert(0, str(_ENGINE))

from src.engines.energy.buildings import country_code_for  # noqa: E402


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
        rows = await conn.fetch(
            """SELECT site_id, site_name, country, region, country_code
                 FROM plenum_cafm.sites ORDER BY site_id"""
        )
        fill: list[tuple[str, str]] = []      # (site_id, code)
        unresolved: list[asyncpg.Record] = []
        conflicts: list[tuple[asyncpg.Record, str]] = []
        from_country = from_region = 0

        for r in rows:
            derived = country_code_for(r["country"])
            source = "country"
            if not derived:
                derived = country_code_for(r["region"])
                source = "region"
            existing = (r["country_code"] or "").strip()
            if existing:
                if derived and derived != existing.upper():
                    conflicts.append((r, derived))
                continue
            if not derived:
                unresolved.append(r)
                continue
            fill.append((r["site_id"], derived))
            if source == "country":
                from_country += 1
            else:
                from_region += 1

        held = sum(1 for r in rows if (r["country_code"] or "").strip())
        print(f"sites: {len(rows)} rows — {held} already have a country_code, "
              f"{len(fill)} can be filled ({from_country} from country, "
              f"{from_region} from region), {len(unresolved)} resolve to nothing.\n")

        by_code: dict[str, int] = {}
        for _, code in fill:
            by_code[code] = by_code.get(code, 0) + 1
        for code, n in sorted(by_code.items(), key=lambda kv: -kv[1]):
            print(f"  {code}  {n}")

        if unresolved:
            print("\n  left null — neither country nor region names one country:")
            for r in unresolved[:12]:
                print(f"    {str(r['site_id'] or ''):<10} {str(r['site_name'] or '')[:24]:<24} "
                      f"country={r['country']!r} region={r['region']!r}")
            if len(unresolved) > 12:
                print(f"    … and {len(unresolved) - 12} more")

        if conflicts:
            print("\n  NOT TOUCHED — a stored code disagrees with the country beside it. "
                  "Settle these by hand:")
            for r, derived in conflicts:
                print(f"    {str(r['site_id'] or ''):<10} stored={r['country_code']!r} "
                      f"country={r['country']!r} would derive {derived!r}")

        print()
        if not args.apply:
            print(f"dry run — nothing written. --apply would fill {len(fill)} row(s).")
            return

        async with conn.transaction():
            tag = await conn.executemany(
                "UPDATE plenum_cafm.sites SET country_code = $2 "
                "WHERE site_id = $1 AND NULLIF(btrim(country_code), '') IS NULL",
                fill,
            )
        _ = tag
        after = await conn.fetchval(
            "SELECT count(*) FROM plenum_cafm.sites "
            "WHERE NULLIF(btrim(country_code), '') IS NOT NULL"
        )
        print(f"filled {len(fill)}. country_code now on {after} of {len(rows)} sites "
              f"(was {held}).")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
