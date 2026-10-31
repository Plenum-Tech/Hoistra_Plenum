"""Which tables an ingest actually wrote to.

Uploading a document is supposed to fan out across plenum_cafm — the file, its extraction,
its chunks, and whatever entities were read out of it. Which of those actually happened is
not visible from the upload's own response, and checking a handful of tables by hand answers
only for the tables you thought to check.

Two modes, because a snapshot taken before an ingest is more reliable than a timestamp and
is not always available:

    python db/tools/what_changed.py --to <dsn> --snapshot before.json
    python db/tools/what_changed.py --to <dsn> --since before.json
    python db/tools/what_changed.py --to <dsn> --minutes 15

``--snapshot`` records every table's row count. ``--since`` re-counts and reports the
difference, which catches inserts into tables that have no timestamp at all. ``--minutes``
needs no baseline and finds rows whose own timestamp is recent, which also catches rows
written before you thought to take one — at the cost of missing any table that does not
record when its rows were made.

Read-only in every mode.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from typing import Any

import asyncpg

#: Columns that mean "when this row was made", in the order they should be trusted.
STAMPS = ("created_at", "uploaded_at", "inserted_at", "timestamp", "updated_at")


def _ssl_for(dsn: str) -> Any:
    host = re.search(r"@([^/:]+)", dsn)
    return "require" if host and not host.group(1).startswith(("localhost", "127.")) else False


async def tables(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        """SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'plenum_cafm' AND table_type = 'BASE TABLE'
            ORDER BY table_name""")
    return [r["table_name"] for r in rows]


async def counts(conn: asyncpg.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in await tables(conn):
        try:
            # The name comes from information_schema and is quoted; nothing here is
            # concatenated from an argument.
            out[t] = await conn.fetchval(f'SELECT count(*) FROM plenum_cafm."{t}"')
        except Exception:  # noqa: BLE001 — a table we cannot read is not a finding
            continue
    return out


async def recent(conn: asyncpg.Connection, minutes: int) -> list[tuple[str, str, int]]:
    found = []
    for t in await tables(conn):
        cols = {r["column_name"] for r in await conn.fetch(
            """SELECT column_name FROM information_schema.columns
                WHERE table_schema='plenum_cafm' AND table_name=$1""", t)}
        stamp = next((c for c in STAMPS if c in cols), None)
        if not stamp:
            continue
        try:
            n = await conn.fetchval(
                f'''SELECT count(*) FROM plenum_cafm."{t}"
                     WHERE "{stamp}" > now() - ($1 || ' minutes')::interval''', str(minutes))
        except Exception:  # noqa: BLE001
            continue
        if n:
            found.append((t, stamp, int(n)))
    return sorted(found, key=lambda r: -r[2])


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", dest="dsn", required=True)
    ap.add_argument("--snapshot", help="write every table's row count to this file")
    ap.add_argument("--since", help="compare against a snapshot file")
    ap.add_argument("--minutes", type=int, help="find rows stamped within this many minutes")
    args = ap.parse_args()

    conn = await asyncpg.connect(args.dsn, ssl=_ssl_for(args.dsn))
    try:
        if args.snapshot:
            now = await counts(conn)
            with open(args.snapshot, "w", encoding="utf-8") as fh:
                json.dump(now, fh, indent=1, sort_keys=True)
            live = {k: v for k, v in now.items() if v}
            print(f"snapshot of {len(now)} tables written to {args.snapshot}")
            print(f"  {len(live)} of them have rows; "
                  f"{sum(now.values()):,} rows in total")

        if args.since:
            if not os.path.exists(args.since):
                raise SystemExit(f"no snapshot at {args.since}")
            with open(args.since, encoding="utf-8") as fh:
                before = json.load(fh)
            after = await counts(conn)
            moved = [(t, before.get(t, 0), n) for t, n in after.items()
                     if n != before.get(t, 0)]
            if not moved:
                print("no table changed row count since the snapshot")
            for t, b, a in sorted(moved, key=lambda r: -(r[2] - r[1])):
                print(f"  {t:34} {b:6} -> {a:6}   {a - b:+d}")

        if args.minutes:
            rows = await recent(conn, args.minutes)
            print(f"\nrows stamped in the last {args.minutes} minutes:")
            if not rows:
                print("  (none — note this only sees tables that record a timestamp)")
            for t, stamp, n in rows:
                print(f"  {t:34} {n:5}  by {stamp}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
