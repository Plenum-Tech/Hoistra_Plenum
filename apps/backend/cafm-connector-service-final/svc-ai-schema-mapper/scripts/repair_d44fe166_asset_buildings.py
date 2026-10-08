"""Repair for migration d44fe166 (21 Sep 2026), production database plenum_agent.

What happened: the writer dropped the workbook's site reference, so ten assets (A-001..A-010)
were inserted with no building — as twins of ten June-import rows that hold the code in `id`,
have no asset_code, and ARE linked to buildings. Every join matching on code then returned
both copies, one with its building and one "not recorded".

What this does, in ONE transaction, rolled back unless every check holds:
  1. the June rows take any model / manufacturer / install date the twin carried that they lacked;
  2. the ten twins are deleted (nothing references them — checked 21 Sep across the 41
     asset_id / parent_asset_id columns in plenum_cafm: zero rows);
  3. the June rows take the code (asset_code = id) — only possible once the twins are gone,
     because assets.asset_code is unique;
  4. the ten work orders on those assets that have no building take their asset's.

Reads the DSN from the repo-root .env (PLENUM_DB_DSN). Run from the repo root:
    python apps/backend/cafm-connector-service-final/svc-ai-schema-mapper/scripts/repair_d44fe166_asset_buildings.py
Pass --dry-run to see the counts and roll back regardless.
"""
import asyncio
import os
import re
import sys

import asyncpg

ROOT_ENV = os.path.join(os.path.dirname(os.path.abspath(__file__)), *([".."] * 5), ".env")
CODES = [f"A-{i:03d}" for i in range(1, 11)]
DRY_RUN = "--dry-run" in sys.argv


def dsn_from_env() -> str:
    with open(ROOT_ENV, encoding="utf-8") as fh:
        raw = next(l.split("=", 1)[1].strip().strip('"') for l in fh if l.startswith("PLENUM_DB_DSN="))
    return re.sub(r"^postgresql\+\w+://", "postgresql://", raw)


async def main() -> None:
    c = await asyncpg.connect(dsn_from_env(), database="plenum_agent", timeout=40)
    try:
        async with c.transaction():
            before = dict(await c.fetchrow(
                "select count(*) total, count(*)-count(building_id) unlinked from plenum_cafm.assets"))

            # 1. the June rows (id = code, no asset_code, linked) take the twin's detail. The code
            #    itself moves in step 3 — assets.asset_code is unique, so it cannot be set while the
            #    twin still holds it (the first dry run failed exactly there).
            n1 = await c.execute("""
                update plenum_cafm.assets j
                   set model = coalesce(j.model, d.model),
                       manufacturer = coalesce(j.manufacturer, d.manufacturer),
                       installation_date = coalesce(j.installation_date, d.installation_date),
                       updated_at = now()
                  from plenum_cafm.assets d
                 where j.id = any($1::text[]) and j.asset_code is null and j.building_id is not null
                   and d.asset_code = j.id and d.id <> d.asset_code and d.building_id is null""", CODES)

            # 2. the twins go
            n2 = await c.execute("""
                delete from plenum_cafm.assets d
                 where d.asset_code = any($1::text[]) and d.building_id is null and d.id <> d.asset_code""", CODES)

            # 3. now the code is free, the June rows take it
            n1b = await c.execute("""
                update plenum_cafm.assets j set asset_code = j.id
                 where j.id = any($1::text[]) and j.asset_code is null and j.building_id is not null""", CODES)

            # 4. the ten work orders inherit their asset's building
            n3 = await c.execute("""
                update plenum_cafm.work_orders w
                   set building_id = a.building_id, updated_at = now()
                  from plenum_cafm.assets a
                 where w.building_id is null and a.id = w.asset_id::text
                   and a.id = any($1::text[]) and a.building_id is not null""", CODES)

            after = dict(await c.fetchrow(
                "select count(*) total, count(*)-count(building_id) unlinked from plenum_cafm.assets"))
            coded = await c.fetchval(
                "select count(*) from plenum_cafm.assets where asset_code = any($1::text[]) "
                "and id = asset_code and building_id is not null", CODES)
            wos = dict(await c.fetchrow(
                "select count(*) n, count(building_id) with_building from plenum_cafm.work_orders "
                "where asset_id::text = any($1::text[])", CODES))
            double_joined = await c.fetchval("""
                select count(*) from (
                  select w.id from plenum_cafm.work_orders w
                    join plenum_cafm.assets a on a.id::text = w.asset_id::text or a.asset_code = w.asset_id::text
                   where w.asset_id::text = any($1::text[]) group by w.id having count(*) > 1) x""", CODES)

            print("assets before:", before, "| after:", after)
            print("june rows detailed:", n1, "| twins deleted:", n2, "| codes moved:", n1b, "| work orders relinked:", n3)
            print("coded+linked June assets:", coded, "| work orders on those codes:", wos,
                  "| double-joined work orders:", double_joined)

            ok = (after["unlinked"] == 0 and coded == 10 and n2 == "DELETE 10"
                  and double_joined == 0 and wos["n"] == wos["with_building"])
            if DRY_RUN:
                raise RuntimeError("dry run — rolling back (checks %s)" % ("hold" if ok else "FAIL"))
            if not ok:
                raise RuntimeError("verification failed — rolling back, nothing changed")
        print("COMMITTED")
    except RuntimeError as e:
        print(str(e))
    finally:
        await c.close()


asyncio.run(main())
