"""Give a site the country its own statutory certificates were issued under.

Two screens disagree about which buildings are in the United Kingdom, and they disagree
because they ask two different rows.

``energy.buildings.list_buildings`` takes the country from ``plenum_cafm.sites``.
``compliance.coverage.building_coverage`` takes it from the certificate — it has to, because
coverage is scored against one country's pack and a certificate that names no country cannot
be scored at all. Five of TechCorp's sites hold a UK certificate and no country of their own,
so Compliance puts them in the UK list and the Buildings page files them under no country.
The two UK lists have nothing in common.

``building_backfill.py`` is explicit that it will not invent a country, and it is right:
guessing "UK" from a region called "South East" puts a benchmark standard on a building on
no evidence. This is not that. A DEC, a TM44, an EICR and a Fire Risk Assessment are
instruments of United Kingdom law, filed against that building, by a named assessor, with a
certificate number. A building cannot hold a DEC and be somewhere else.

So the rule here is narrow on purpose:

  * only a **Building-scope** certificate counts — a vendor accreditation names the
    vendor's country, not the property's;
  * every such certificate on the building must name the **same** country, or the site is
    reported as contested and left alone;
  * only a NULL is ever filled — a site that already states a country keeps it;
  * ``state``, ``region`` and ``city`` come across the same way and under the same rule,
    because the certificate carries them and the site does not.

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

#: (column of the EVIDENCE query below, column on sites it fills). Only ever into a NULL.
#:
#: Spelled as the query's own aliases rather than as the certificate's column names: they
#: differ (`cert_country` carries `compliance_certificates.country_code`), and a lookup
#: built by pasting a prefix onto the wrong half quietly found nothing, set nothing, and
#: was caught only by the verification at the end of the transaction.
CARRY: tuple[tuple[str, str], ...] = (
    ("cert_country", "country_code"),
    ("cert_state", "state"),
    ("cert_region", "region"),
)

#: One row per site whose country can be read off its own building-scope certificates.
#: `bool_and` over the distinct countries is what makes a contested site fall out: a site
#: whose certificates disagree produces more than one, and the HAVING drops it.
EVIDENCE = f"""
    SELECT s.site_id,
           s.site_name,
           s.country_code                              AS site_country,
           min(c.country_code)                         AS cert_country,
           min(c.state)                                AS cert_state,
           min(c.region)                               AS cert_region,
           count(*)                                    AS certificates,
           count(DISTINCT c.country_code)              AS countries_named,
           string_agg(DISTINCT c.certificate_type_code, ', ' ORDER BY
                      c.certificate_type_code)         AS types
      FROM {SCHEMA}.sites s
      JOIN {SCHEMA}.buildings b   ON b.site_id = s.site_id
      JOIN {SCHEMA}.compliance_certificates c
        ON c.building_id = b.building_id
       AND c.cert_scope = 'Building'
       AND nullif(c.country_code, '') IS NOT NULL
       AND coalesce(c.status, '') <> 'archived'
     WHERE s.country_code IS NULL
     GROUP BY s.site_id, s.site_name, s.country_code
     ORDER BY s.site_id
"""


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        await c.close()
        return 2
    print(f"database: {db}\n")

    rows = await c.fetch(EVIDENCE)
    agreed = [r for r in rows if r["countries_named"] == 1]
    contested = [r for r in rows if r["countries_named"] > 1]

    print(f"{'site':<10} {'name':<22} {'from':<5} {'state':<10} {'region':<12} evidence")
    for r in agreed:
        print(f"  {r['site_id']:<8} {str(r['site_name'])[:20]:<22} {r['cert_country']:<5} "
              f"{str(r['cert_state'] or '-'):<10} {str(r['cert_region'] or '-'):<12} {r['types']}")
    for r in contested:
        print(f"  {r['site_id']:<8} {str(r['site_name'])[:20]:<22} CONTESTED — its certificates "
              f"name {r['countries_named']} countries ({r['types']}); left alone")

    print(f"\n  {len(agreed)} sites would take a country from their own certificates, "
          f"{len(contested)} contested")

    # What this changes downstream, said before it is done rather than discovered after.
    if agreed:
        codes: dict[str, int] = {}
        for r in agreed:
            codes[r["cert_country"]] = codes.get(r["cert_country"], 0) + 1
        print(f"  they would join the Buildings page's country views as: {codes}")
        print("  and the ratings tiles for those countries would start counting them")

    if not apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        filled = 0
        for r in agreed:
            sets, params = [], [r["site_id"]]
            # `x in record` on an asyncpg Record tests its VALUES, not its keys — so a
            # membership test spelled that way answers False for every column name there is.
            available = set(r.keys())
            for src, dst in CARRY:
                value = r[src] if src in available else None
                if value in (None, ""):
                    continue
                params.append(value)
                # Only ever into a NULL: a site that already states this keeps what it has.
                sets.append(f'"{dst}" = coalesce("{dst}", ${len(params)})')
            if not sets:
                continue
            await c.execute(
                f'UPDATE {SCHEMA}.sites SET {", ".join(sets)} WHERE site_id = $1', *params)
            filled += 1

        left = await c.fetch(EVIDENCE)
        still = [x for x in left if x["countries_named"] == 1]
        if still:
            await tr.rollback()
            print(f"ROLLED BACK — {len(still)} sites still have no country after the update")
            await c.close()
            return 1
        await tr.commit()
    except Exception as exc:  # noqa: BLE001
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    print(f"  {filled} sites now name the country their certificates were issued under")
    for r in await c.fetch(
        f"""SELECT country_code, count(*) n FROM {SCHEMA}.sites
             WHERE country_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC"""):
        print(f"    {r['country_code']:<6} {r['n']}")
    print(f"    (no country) "
          f"{await c.fetchval(f'SELECT count(*) FROM {SCHEMA}.sites WHERE country_code IS NULL')}")
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
