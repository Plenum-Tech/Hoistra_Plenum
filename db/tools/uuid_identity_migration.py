"""Bring a legacy integer-keyed deployment onto the platform's uuid identity model.

Two identity models are in the same database and they cannot see each other. ``organizations``
and ``users`` are integer-keyed — the original connector schema — while the 76 tables added
for Features A/B/C carry ``organization_id uuid``, as do every table ``access_control.sql``
added and every line of the auth layer (``Principal.user_id: UUID``, every scoped query's
``CAST(:x AS uuid)``). Postgres will not compare the two: a join across them raises
``operator does not exist: integer = uuid``, which is why Users & access, Super Admin, the
audit trail and invitations cannot work on such a deployment, and why Compliance and Vendors
show nothing — their rows are keyed to a company id that cannot be resolved to a company.

There is no halfway fix. Making the new tables integer-keyed would leave the 76 existing ones
stranded and fork the identity model through the whole service; the platform's own convention
(CLAUDE.md §20) is uuid keys, and the test database already is uuid throughout. So this moves
the two legacy tables, and everything that points at them, to uuid.

**The mapping is read off the data, not invented.** Rows written by the newer features
already refer to companies as ``00000000-0000-0000-0000-0000000000NN`` — 5,031 rows use
``…0001`` and four use ``…0005``, which are organizations 1 and 5 written as uuids. This
migration makes that the actual key, so those rows resolve the moment it runs, with no row
rewritten to mean something it did not mean before:

    organizations.id  n  ->  00000000-0000-0000-0000-<n as 12 hex digits>
    users.id          n  ->  00000000-0000-0000-0001-<n as 12 hex digits>

Users take a different prefix so a user id and a company id can never be mistaken for one
another when reading a row by eye.

**What it deliberately does NOT do.** Some rows carry company ids that match no organisation
— ``…00b1`` on 2,915 Feature B rows, with ``…00f1`` as an "approver" that matches no user,
plus about twenty one-off values. They are placeholders from demo seeding. This migration
leaves every one of them exactly as it is: they resolve to nothing before and to nothing
after. Deciding what they represent is a question for whoever seeded that data, and guessing
would silently attach demo rows to a real tenant.

Dry run by default. ``--apply`` executes, in one transaction, and verifies before committing.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any

import asyncpg

SCHEMA = "plenum_cafm"
ORG_PREFIX = "00000000-0000-0000-0000-"
USER_PREFIX = "00000000-0000-0000-0001-"
#: Columns that hold a user, found by name. The foreign keys are authoritative; this catches
#: the ones nobody constrained.
USER_COLUMN_NAMES = (
    "user_id", "actor_user_id", "created_by", "invited_by", "approved_by", "granted_by",
    "accepted_user_id", "requested_by_id", "completed_by_id", "decided_by", "changed_by",
    "performed_by",
)


def uuid_expr(column: str, prefix: str) -> str:
    """SQL that turns an integer key into its uuid, deterministically and reversibly."""
    return f"('{prefix}' || lpad(to_hex({column}), 12, '0'))::uuid"


async def plan(c: asyncpg.Connection) -> dict[str, Any]:
    org_type = await c.fetchval("""
        SELECT data_type FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'organizations' AND column_name = 'id'""", SCHEMA)
    user_type = await c.fetchval("""
        SELECT data_type FROM information_schema.columns
         WHERE table_schema = $1 AND table_name = 'users' AND column_name = 'id'""", SCHEMA)

    fks = await c.fetch("""
        SELECT con.conname, src.relname AS from_table, tgt.relname AS to_table,
               pg_get_constraintdef(con.oid) AS def
          FROM pg_constraint con
          JOIN pg_class src ON src.oid = con.conrelid
          JOIN pg_class tgt ON tgt.oid = con.confrelid
          JOIN pg_namespace n ON n.oid = src.relnamespace
         WHERE con.contype = 'f' AND n.nspname = $1
           AND tgt.relname IN ('organizations', 'users')
         ORDER BY src.relname, con.conname""", SCHEMA)

    org_cols = await c.fetch("""
        SELECT c.table_name, c.data_type, c.is_nullable
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           AND t.table_type = 'BASE TABLE'
         WHERE c.table_schema = $1 AND c.column_name = 'organization_id'
           AND c.data_type <> 'uuid'
         ORDER BY c.table_name""", SCHEMA)

    user_cols = await c.fetch("""
        SELECT c.table_name, c.column_name, c.data_type
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
           AND t.table_type = 'BASE TABLE'
         WHERE c.table_schema = $1 AND c.column_name = ANY($2::text[])
           AND c.data_type = 'integer'
           AND NOT (c.table_name = 'users' AND c.column_name = 'id')
         ORDER BY c.table_name, c.column_name""", SCHEMA, list(USER_COLUMN_NAMES))

    views = await c.fetch("""
        SELECT DISTINCT v.relname
          FROM pg_depend d
          JOIN pg_rewrite rw ON rw.oid = d.objid
          JOIN pg_class v ON v.oid = rw.ev_class
          JOIN pg_class t ON t.oid = d.refobjid
          JOIN pg_namespace n ON n.oid = t.relnamespace
         WHERE n.nspname = $1 AND t.relname IN ('organizations', 'users') AND v.relkind = 'v'""",
        SCHEMA)

    return {"org_type": org_type, "user_type": user_type, "fks": fks, "org_cols": org_cols,
            "user_cols": user_cols, "views": views}


async def text_column_values(c: asyncpg.Connection, table: str) -> list[str]:
    rows = await c.fetch(
        f'SELECT DISTINCT organization_id FROM {SCHEMA}."{table}" WHERE organization_id IS NOT NULL LIMIT 20')
    return [str(r[0]) for r in rows]


async def statements(c: asyncpg.Connection, p: dict[str, Any]) -> list[str]:
    """Every statement the migration would run, in order."""
    out: list[str] = []

    # 1. the constraints have to come off before either key can change type
    for f in p["fks"]:
        out.append(f'ALTER TABLE {SCHEMA}."{f["from_table"]}" DROP CONSTRAINT "{f["conname"]}";')

    # 2. the keys themselves, and the sequences that fed them
    if p["org_type"] == "integer":
        out += [
            f'ALTER TABLE {SCHEMA}.organizations ALTER COLUMN id DROP DEFAULT;',
            f'ALTER TABLE {SCHEMA}.organizations ALTER COLUMN id TYPE uuid USING {uuid_expr("id", ORG_PREFIX)};',
            f'ALTER TABLE {SCHEMA}.organizations ALTER COLUMN id SET DEFAULT gen_random_uuid();',
            f'DROP SEQUENCE IF EXISTS {SCHEMA}.organizations_id_seq;',
        ]
    if p["user_type"] == "integer":
        out += [
            f'ALTER TABLE {SCHEMA}.users ALTER COLUMN id DROP DEFAULT;',
            f'ALTER TABLE {SCHEMA}.users ALTER COLUMN id TYPE uuid USING {uuid_expr("id", USER_PREFIX)};',
            f'ALTER TABLE {SCHEMA}.users ALTER COLUMN id SET DEFAULT gen_random_uuid();',
            f'DROP SEQUENCE IF EXISTS {SCHEMA}.users_id_seq;',
        ]

    # 3. every column that points at a company
    for col in p["org_cols"]:
        t = col["table_name"]
        if col["data_type"] == "integer":
            using = uuid_expr("organization_id", ORG_PREFIX)
        else:
            # text: a value that already looks like a uuid is taken as one; an integer in a
            # text column is mapped the same way as an integer column.
            values = await text_column_values(c, t)
            looks_uuid = all(re.fullmatch(r"[0-9a-fA-F-]{36}", v) for v in values) if values else True
            using = ("NULLIF(organization_id, '')::uuid" if looks_uuid
                     else uuid_expr("NULLIF(organization_id, '')::int", ORG_PREFIX))
        out.append(f'ALTER TABLE {SCHEMA}."{t}" ALTER COLUMN organization_id TYPE uuid USING {using};')

    # 4. every column that points at a user
    for col in p["user_cols"]:
        out.append(
            f'ALTER TABLE {SCHEMA}."{col["table_name"]}" ALTER COLUMN "{col["column_name"]}" '
            f'TYPE uuid USING {uuid_expr(col["column_name"], USER_PREFIX)};')

    # 5. put the constraints back exactly as they were
    for f in p["fks"]:
        out.append(f'ALTER TABLE {SCHEMA}."{f["from_table"]}" ADD CONSTRAINT "{f["conname"]}" {f["def"]};')

    # 6. a building belongs to the company that owns its site.
    #
    # access_control.sql added buildings.organization_id and backfilled it only where exactly
    # one company exists - with two, it correctly refused to guess. No guess is needed here:
    # every building hangs off a site and sites.organization_id is set on 617 of 626 rows.
    # Derived, not assumed, and only ever filling a NULL.
    out.append(
        f'UPDATE {SCHEMA}.buildings b SET organization_id = s.organization_id '
        f'FROM {SCHEMA}.sites s WHERE b.organization_id IS NULL '
        f'AND s.organization_id IS NOT NULL '
        f'AND (s.id = b.site_id OR s.site_id = b.site_id);')
    return out


async def verify(c: asyncpg.Connection) -> list[str]:
    """What must be true afterwards. Any failure here rolls the whole thing back."""
    problems: list[str] = []
    org_type = await c.fetchval("""SELECT data_type FROM information_schema.columns
        WHERE table_schema = $1 AND table_name='organizations' AND column_name='id'""", SCHEMA)
    user_type = await c.fetchval("""SELECT data_type FROM information_schema.columns
        WHERE table_schema = $1 AND table_name='users' AND column_name='id'""", SCHEMA)
    if org_type != "uuid":
        problems.append(f"organizations.id is {org_type}")
    if user_type != "uuid":
        problems.append(f"users.id is {user_type}")

    left = await c.fetch("""
        SELECT c.table_name, c.data_type FROM information_schema.columns c
          JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
         WHERE c.table_schema=$1 AND c.column_name='organization_id' AND c.data_type<>'uuid'
           AND t.table_type='BASE TABLE'""", SCHEMA)
    if left:
        problems.append("still not uuid: " + ", ".join(f"{r['table_name']}({r['data_type']})" for r in left))

    # the join that could not run before
    try:
        await c.fetchval(f"""SELECT count(*) FROM {SCHEMA}.buildings b
                              JOIN {SCHEMA}.organizations o ON o.id = b.organization_id""")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"buildings JOIN organizations still fails: {str(exc)[:90]}")

    return problems


async def resolvable(c: asyncpg.Connection) -> tuple[int, int]:
    """Rows whose organization_id names an organisation that exists, and rows that set one.

    This is the whole point of the migration, so it is measured rather than asserted: before
    it runs the uuid-keyed tables can resolve nothing at all, because the key they refer to
    is a different type from the one organizations holds.
    """
    cols = await c.fetch("""
        SELECT c.table_name FROM information_schema.columns c
          JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
         WHERE c.table_schema=$1 AND c.column_name='organization_id' AND c.data_type='uuid'
           AND t.table_type='BASE TABLE' ORDER BY c.table_name""", SCHEMA)
    ids = [r["id"] for r in await c.fetch(f"SELECT id::text AS id FROM {SCHEMA}.organizations")]
    hit = total = 0
    for r in cols:
        t = r["table_name"]
        try:
            total += await c.fetchval(
                f'SELECT count(*) FROM {SCHEMA}."{t}" WHERE organization_id IS NOT NULL')
            hit += await c.fetchval(
                f'SELECT count(*) FROM {SCHEMA}."{t}" WHERE organization_id::text = ANY($1::text[])', ids)
        except Exception:  # noqa: BLE001 — a table that cannot be read is not a measurement
            continue
    return hit, total


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        return 2
    print(f"database: {db}")

    p = await plan(c)
    print(f"organizations.id: {p['org_type']}   users.id: {p['user_type']}")
    if p["org_type"] == "uuid" and p["user_type"] == "uuid":
        print("already on the uuid identity model — nothing to do.")
        await c.close()
        return 0
    if p["views"]:
        print("views depend on these tables and would need recreating:",
              [r["relname"] for r in p["views"]])
        await c.close()
        return 2

    before = {}
    for t in ("organizations", "users", "buildings", "sites", "compliance_certificates",
              "work_orders", "assets", "vendors"):
        before[t] = await c.fetchval(f'SELECT count(*) FROM {SCHEMA}."{t}"')

    hit_before, total_before = await resolvable(c)
    print(f"rows naming a company that exists: {hit_before} of {total_before} that name one")

    sql = await statements(c, p)
    print(f"\n{len(sql)} statements: {len(p['fks'])} constraints off and back on, "
          f"{len(p['org_cols'])} company columns, {len(p['user_cols'])} user columns")
    for s in sql[:6]:
        print("   ", s[:118])
    print("    …")
    for s in sql[-3:]:
        print("   ", s[:118])

    if not apply:
        print("\ndry run — nothing executed. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        for i, s in enumerate(sql, 1):
            await c.execute(s)
            if i % 20 == 0:
                print(f"    {i}/{len(sql)}")
        problems = await verify(c)
        hit_after, total_after = await resolvable(c)
        print(f"    rows naming a company that exists: {hit_before} -> {hit_after} "
              f"(of {total_after} that name one at all)")
        if total_after and hit_after < hit_before:
            problems.append(f"fewer rows resolve than before: {hit_before} -> {hit_after}")
        after = {t: await c.fetchval(f'SELECT count(*) FROM {SCHEMA}."{t}"') for t in before}
        lost = {t: (before[t], after[t]) for t in before if before[t] != after[t]}
        if lost:
            problems.append(f"row counts changed: {lost}")
        if problems:
            await tr.rollback()
            print("ROLLED BACK — verification failed:")
            for x in problems:
                print("   ", x)
            await c.close()
            return 1
        await tr.commit()
    except Exception as exc:
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    print("committed. Row counts unchanged:", before)
    for r in await c.fetch(f"SELECT id::text, name FROM {SCHEMA}.organizations ORDER BY name"):
        n = await c.fetchval(f'SELECT count(*) FROM {SCHEMA}.buildings WHERE organization_id = $1::uuid',
                             r["id"])
        print(f"   {r['id']}  {r['name']:<28} buildings={n}")
    await c.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", default="plenum_agent", help="the database this must be run against")
    ap.add_argument("--dsn", default=None, help="override the DSN (defaults to apps/backend/.env DB_URL)")
    ap.add_argument("--apply", action="store_true", help="execute; without it this is a dry run")
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
