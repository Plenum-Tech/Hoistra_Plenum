"""Push this Trace Lab's eval store to another Trace Lab database (11 Nov 2026, task plan B5).

Results made on one machine (a local run of scenarios) are read on another (the development instance). The other
database's address is read from the environment, DEV_DB_URL, when a push starts; it is never in the code, never
stored and never logged. What is shown of it anywhere is "host/database".

    sessions, traces, observations (the spans), scores (the annotations too), and what they hang from:
    run tags, the sections of the specification, scenarios

Every one of those tables is append-only, so "upsert by id" is: insert the row under its own key, or leave the one
that is there. A push therefore cannot duplicate and cannot overwrite, and one that stops half way is finished by the
next. Tables go in the order their references need. Rows travel as the database's own JSON of the row
(to_jsonb / jsonb_populate_record), never through Python numbers, so a value arrives exactly as it was.

Only rows made since the last push that finished are read (minus a few minutes of overlap); `full` reads everything.
The time of each push, what it sent and how it ended is kept in tracelab.sync_log, here only.

Before anything is written the push refuses: the same database as this one, a database with no Trace Lab in it, one
on a different migration, or one that cannot be reached.

Command line (from svc-trace-lab, with TRACE_LAB_DB_URL and DEV_DB_URL set):

    python -m trace_lab.sync_push [--dry-run] [--full]

DEV_DB_URL is the other instance's TRACE LAB database (schema tracelab), never the application database. This module
never opens the application database at all.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from . import db, labelling, migrate

log = structlog.get_logger(__name__)

ENV = "DEV_DB_URL"
#: In the order their references need: a session names a run tag and a scenario, a trace its session, an
#: observation and a score their trace.
#: Failure-mode labels on traces (trace_label_cells, migration 0015) are NOT pushed: a label names its mode by lineage,
#: and the modes themselves (lab_failure_modes) are numbered per database and not pushed, so on the dev database a
#: pushed label would point at a mode it does not have and never show. They join this list when the modes travel too.
TABLES = ("run_tags", "spec_sections", "scenarios", "sessions", "traces", "observations", "scores")
PKS = {"run_tags": ("tag",), "spec_sections": ("spec_version", "section_id"), "scenarios": ("scenario_id", "version"),
       "sessions": ("id",), "traces": ("id",), "observations": ("trace_id", "id"), "scores": ("id",)}
#: What a person calls each table's rows, in the order the summary names them. Observations are "spans".
LABELS = (("sessions", "session", "sessions"), ("traces", "trace", "traces"), ("observations", "span", "spans"),
          ("scores", "score", "scores"), ("scenarios", "scenario", "scenarios"), ("run_tags", "run tag", "run tags"),
          ("spec_sections", "spec section", "spec sections"))
#: Columns that only say when a row was made and by which machine or person. The same row made on two machines
#: differs in these and in nothing else, so they are left out when two rows are compared.
BOOKKEEPING = ("created_at", "created_by", "created_by_email", "origin")
#: SQL that takes those keys out of a row's jsonb, one key at a time by its literal name. Taking them out with a bound
#: text[] (jsonb - $n::text[]) crashed Postgres 16.2 (signal 11, every connection on the server dropped) on one batch of
#: 500 scenarios with half of them already on the destination; this form does not.
_WITHOUT_BOOKKEEPING = "".join(f" - '{key}'" for key in BOOKKEEPING)
#: The rows a row cannot exist without: (its columns, the other table, that table's columns).
PARENTS: dict[str, tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...]] = {
    "sessions": ((("run_tag",), "run_tags", ("tag",)),
                 (("scenario_id", "scenario_version"), "scenarios", ("scenario_id", "version"))),
    "traces": ((("session_id",), "sessions", ("id",)),),
    "observations": ((("trace_id",), "traces", ("id",)),),
    "scores": ((("trace_id",), "traces", ("id",)), (("session_id",), "sessions", ("id",))),
}
BATCH = 500
BATCH_BYTES = 4_000_000
OVERLAP = timedelta(minutes=5)
APPLICATION_NAME = "trace-lab-sync"


class Refused(Exception):
    """A push that was not started. `reason` is the code, `error` the sentence a person reads (never the address)."""

    def __init__(self, reason: str, error: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.error = error


# ── the address ──────────────────────────────────────────────────────────────────────

def dest_url(env: Mapping[str, str] | None = None) -> str:
    """DEV_DB_URL as the address to connect to. Refused when it is unset, or names anything but one host and one
    database: a ?host= or ?database= parameter would send the push somewhere other than what is shown."""
    raw = ((os.environ if env is None else env).get(ENV) or "").strip()
    if not raw:
        raise Refused("not_configured", f"{ENV} is not set, so there is nowhere to push to.")
    for plain in ("postgresql://", "postgres://"):
        if raw.startswith(plain):
            raw = "postgresql+asyncpg://" + raw[len(plain):]
    u = migrate._parse(raw)
    if (u is None or u.drivername != "postgresql+asyncpg" or not u.host or not u.database or "#" in raw
            or migrate._override_params(raw) or migrate._OVERRIDE_TEXT_RE.search(raw)):
        raise Refused("bad_url", f"{ENV} must be a postgresql address that names one host and one database, with no "
                                 "host or database parameters.")
    return raw


def peer_name(url: str) -> str:
    """host/database (host:port/database when the port is not the usual one, so two servers on one host are two
    places): all that is ever shown, stored or logged of the other database's address."""
    u = migrate._parse(url)
    if u is None:
        return "unknown"
    port = f":{u.port}" if u.port and int(u.port) != 5432 else ""
    return f"{u.host}{port}/{u.database}"


def _where(url_obj: Any) -> tuple[str, int, str]:
    return ((url_obj.host or "").lower(), int(url_obj.port or 5432), url_obj.database or "")


def failure_kind(exc: BaseException) -> str:
    """What went wrong, as a kind. A driver's own words can quote a row (one company's answer) or the address, and
    the result is stored and shown, so they are never used."""
    orig = getattr(exc, "orig", None)
    state = str(getattr(orig, "sqlstate", None) or getattr(getattr(orig, "__cause__", None), "sqlstate", None)
                or getattr(exc, "sqlstate", None) or "")
    names = type(exc).__name__ + " " + type(orig).__name__
    if isinstance(exc, TimeoutError) or state == "57014":
        return "it took too long"
    if state == "42501":
        return "the login is not allowed to write there"
    if state.startswith("23"):
        return "the dev database refused a row"
    if state.startswith(("08", "57P")) or isinstance(exc, OSError) or "Connection" in names or "Interface" in names:
        return "the connection was lost"
    return f"an unexpected error ({type(exc).__name__})"


# ── what is checked before anything is written ────────────────────────────────────────

async def _schema_version(engine: AsyncEngine) -> int | None:
    """The newest migration applied to a database, or None when it has no Trace Lab schema."""
    async with engine.connect() as conn:
        there = (await conn.execute(text("SELECT to_regclass('tracelab.schema_migrations') IS NOT NULL"))).scalar()
        if not there:
            return None
        return (await conn.execute(text("SELECT max(version) FROM tracelab.schema_migrations"))).scalar()


async def preflight(dest: AsyncEngine) -> None:
    """Refuse a push that must not start: the same database, one that cannot be reached, one with no Trace Lab in
    it, or one on a different migration."""
    if _where(dest.url) == _where(db.lab_engine().url):
        raise Refused("same_database", "The dev database is this database. Point the push at the other instance.")
    try:
        theirs = await _schema_version(dest)
    except Refused:
        raise
    except Exception as exc:                      # noqa: BLE001 - any connection failure is the same answer
        log.warning("trace_lab.sync.unreachable", error=type(exc).__name__)
        raise Refused("unreachable", "The dev database could not be reached. Check the address, the login and "
                                     "that this machine is allowed to connect.") from None
    if theirs is None:
        raise Refused("schema_absent", "The dev database has no Trace Lab in it. Apply the migrations there first.")
    ours = await _schema_version(db.lab_engine())
    if theirs != ours:
        raise Refused("schema_version", f"The dev database is on schema {theirs} and this one is on {ours}. "
                                        "Apply the same migrations to both, then push again.")


# ── the log, and what counts as new ───────────────────────────────────────────────────

async def _log(peer: str, *, dry_run: bool, status: str, watermark: datetime | None, pushed: dict[str, int],
               error: str | None, by: labelling.Member | None, started: datetime,
               conflicts: dict[str, int] | None = None) -> None:
    async with db.lab_engine().begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.sync_log (peer, dry_run, status, watermark, pushed, error, started_by,"
            " started_by_email, started_at) VALUES (:peer, :dry, :status, :mark, CAST(:pushed AS jsonb), :error,"
            " CAST(:by AS uuid), :email, :started)"),
            {"peer": peer[:300], "dry": dry_run, "status": status, "mark": watermark,
             "pushed": json.dumps({**pushed, "conflicts": conflicts or {}}),
             "error": (error or None) and error[:2000], "by": by.user_id if by else None,
             "email": by.email if by else None, "started": started})


async def since(full: bool = False) -> datetime | None:
    """Rows made after this are read: the start of the last push to this destination that finished, less OVERLAP.
    None reads everything: a full push, a destination never pushed to, or none configured."""
    if full:
        return None
    try:
        peer = peer_name(dest_url())
    except Refused:
        return None
    async with db.lab_engine().connect() as conn:
        mark = (await conn.execute(text(
            "SELECT max(watermark) FROM tracelab.sync_log WHERE status = 'done' AND NOT dry_run AND peer = :peer"),
            {"peer": peer})).scalar()
    return (mark - OVERLAP) if mark is not None else None


# ── reading here, writing there ───────────────────────────────────────────────────────

async def _read_batches(conn: Any, table: str, mark: datetime | None) -> AsyncIterator[list[str]]:
    """The table's rows as JSON texts, oldest first, in batches bounded by count and by size."""
    order = ", ".join(f"t.{c}" for c in PKS[table])
    where = "WHERE t.created_at > :mark" if mark is not None else ""
    offset = 0
    while True:
        params: dict[str, Any] = {"lim": BATCH, "off": offset}
        if mark is not None:
            params["mark"] = mark
        rows = [r[0] for r in (await conn.execute(text(
            f"SELECT to_jsonb(t)::text FROM tracelab.{table} t {where} ORDER BY t.created_at, {order}"
            " LIMIT :lim OFFSET :off"), params)).all()]
        if not rows:
            return
        offset += len(rows)
        batch: list[str] = []
        size = 0
        for row in rows:
            if batch and size + len(row) > BATCH_BYTES:
                yield batch
                batch, size = [], 0
            batch.append(row)
            size += len(row)
        if batch:
            yield batch
        if len(rows) < BATCH:
            return


def _rows_sql(table: str) -> str:
    return (f"jsonb_array_elements(CAST(:rows AS jsonb)) AS r(value),"
            f" LATERAL jsonb_populate_record(NULL::tracelab.{table}, r.value) AS p")


def _has_parents(table: str) -> str:
    """SQL that is true for a row `p` whose parent rows are all on the destination."""
    parts = []
    for cols, other, keys in PARENTS.get(table, ()):
        match = " AND ".join(f"x.{k} = p.{c}" for c, k in zip(cols, keys))
        parts.append(f"(p.{cols[0]} IS NULL OR EXISTS (SELECT 1 FROM tracelab.{other} x WHERE {match}))")
    return " AND ".join(parts) or "TRUE"


async def _insert_batch(conn: Any, table: str, rows: list[str]) -> int:
    """Insert the rows the destination does not have and can take; say how many that was. A row whose place is
    taken (its key, or its turn in its session) is left out, and so is a row whose parent was: neither is an error,
    and both are counted by _count_conflicts."""
    r = await conn.execute(text(
        f"INSERT INTO tracelab.{table} SELECT p.* FROM {_rows_sql(table)} WHERE {_has_parents(table)}"
        " ON CONFLICT DO NOTHING"), {"rows": "[" + ",".join(rows) + "]"})
    return max(r.rowcount, 0)


async def _count_new(conn: Any, table: str, rows: list[str]) -> int:
    """How many of the rows the destination does not have by their key (a dry run writes nothing)."""
    same = " AND ".join(f"x.{c}::text = r.value->>'{c}'" for c in PKS[table])
    return int((await conn.execute(text(
        f"SELECT count(*) FROM jsonb_array_elements(CAST(:rows AS jsonb)) AS r(value)"
        f" WHERE NOT EXISTS (SELECT 1 FROM tracelab.{table} x WHERE {same})"),
        {"rows": "[" + ",".join(rows) + "]"})).scalar() or 0)


async def _count_conflicts(conn: Any, table: str, rows: list[str], *, placed: bool) -> int:
    """How many of the rows the destination holds differently: under the same key but saying something else (apart
    from when and where the row was made), or, after an insert (`placed`), not there at all because something else
    stands in their place. Nothing on the destination is ever changed, so these are what a person must be told."""
    same = " AND ".join(f"x.{c} = p.{c}" for c in PKS[table])
    absent = f"x.{PKS[table][0]} IS NULL"
    differs = f"(to_jsonb(x){_WITHOUT_BOOKKEEPING}) <> (to_jsonb(p){_WITHOUT_BOOKKEEPING})"
    where = f"{absent} OR {differs}" if placed else f"NOT ({absent}) AND {differs}"
    return int((await conn.execute(text(
        f"SELECT count(*) FROM {_rows_sql(table)} LEFT JOIN tracelab.{table} x ON {same} WHERE {where}"),
        {"rows": "[" + ",".join(rows) + "]"})).scalar() or 0)


async def push(*, dry_run: bool = False, full: bool = False, by: labelling.Member | None = None) -> dict[str, Any]:
    """Push the eval store to DEV_DB_URL. Returns {"status": "done" | "failed", "peer", "dry_run", "pushed": {table:
    rows the destination took}, "conflicts": {table: rows left out because the destination holds something else in
    their place}, "error": the kind of failure, "watermark"}. Raises Refused when the push must not start.

    A dry run counts as new every row the destination does not have by its key; whether such a row's place is taken
    by another row is only known when it is written."""
    started = datetime.now(timezone.utc)
    url = dest_url()
    peer = peer_name(url)
    dest = db._engine_for(url, pool_size=1, max_overflow=0, server_settings={"application_name": APPLICATION_NAME})
    pushed = {t: 0 for t in TABLES}
    conflicts = {t: 0 for t in TABLES}
    try:
        try:
            await preflight(dest)
        except Refused as refused:
            await _log(peer, dry_run=dry_run, status="refused", watermark=None, pushed=pushed, error=refused.error,
                       by=by, started=started)
            raise
        mark = await since(full=full)
        error: str | None = None
        read_at: datetime | None = None
        table = TABLES[0]
        try:
            async with db.lab_engine().connect() as local:
                local = await local.execution_options(isolation_level="REPEATABLE READ")
                async with local.begin():                       # one snapshot: a session and its traces together
                    # What counts as new next time is measured on this database's clock, the one that stamps
                    # created_at, not on this machine's: the two can disagree by more than the overlap.
                    read_at = (await local.execute(text("SELECT now()"))).scalar()
                    for table in TABLES:
                        async for rows in _read_batches(local, table, mark):
                            if dry_run:
                                async with dest.connect() as conn:
                                    pushed[table] += await _count_new(conn, table, rows)
                                    conflicts[table] += await _count_conflicts(conn, table, rows, placed=False)
                            else:
                                async with dest.begin() as conn:
                                    pushed[table] += await _insert_batch(conn, table, rows)
                                    conflicts[table] += await _count_conflicts(conn, table, rows, placed=True)
        except Exception as exc:                  # noqa: BLE001 - reported and logged; the next push finishes it
            error = f"{table}: {failure_kind(exc)}"
            log.warning("trace_lab.sync.failed", peer=peer, table=table, error=type(exc).__name__)
        status = "failed" if error else "done"
        watermark = read_at if status == "done" and not dry_run else None
        await _log(peer, dry_run=dry_run, status=status, watermark=watermark, pushed=pushed, error=error, by=by,
                   started=started, conflicts=conflicts)
        log.info("trace_lab.sync.pushed", peer=peer, status=status, dry_run=dry_run,
                 conflicts=sum(conflicts.values()), **pushed)
        return {"status": status, "peer": peer, "dry_run": dry_run, "pushed": pushed, "conflicts": conflicts,
                "error": error, "watermark": watermark.isoformat() if watermark else None}
    finally:
        await dest.dispose()


async def status() -> dict[str, Any]:
    """Whether there is somewhere to push to, where (host/database), and how the last push there ended."""
    try:
        peer = peer_name(dest_url())
    except Refused:
        return {"enabled": False, "peer": None, "last": None}
    async with db.lab_engine().connect() as conn:
        row = (await conn.execute(text(
            "SELECT status, dry_run, pushed::text AS pushed, error, finished_at FROM tracelab.sync_log"
            " WHERE peer = :peer ORDER BY finished_at DESC LIMIT 1"), {"peer": peer})).mappings().first()
    last = None
    if row is not None:
        stored = json.loads(row["pushed"] or "{}")
        left_out = stored.pop("conflicts", None) or {}
        last = {"status": row["status"], "dry_run": row["dry_run"], "pushed": stored, "conflicts": left_out,
                "error": row["error"], "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None}
    return {"enabled": True, "peer": peer, "last": last}


def summary_line(out: Mapping[str, Any]) -> str:
    """The one line a person reads after a push."""
    pushed = out.get("pushed") or {}
    peer = out.get("peer") or "the dev database"
    if out.get("status") == "failed":
        return f"The push to {peer} stopped part of the way ({out.get('error') or 'unknown error'}). Run it again to finish."
    parts = [f"{int(pushed.get(key) or 0):,} {one if int(pushed.get(key) or 0) == 1 else many}" for key, one, many in LABELS]
    line = f"{'Would push' if out.get('dry_run') else 'Pushed'} to {peer}: {', '.join(parts)}."
    left_out = {key: int(n or 0) for key, n in (out.get("conflicts") or {}).items()}
    total = sum(left_out.values())
    if total:
        named = ", ".join(f"{left_out[key]:,} {one if left_out[key] == 1 else many}" for key, one, many in LABELS
                          if left_out.get(key))
        verb = "would be" if out.get("dry_run") else ("was" if total == 1 else "were")
        line += (f" {total:,} {'row' if total == 1 else 'rows'} {verb} left out because the dev database already holds"
                 f" something else in {'its' if total == 1 else 'their'} place ({named}). Nothing there was changed.")
    return line


def main(argv: list[str] | None = None) -> int:
    """python -m trace_lab.sync_push [--dry-run] [--full]. Exit 0 done, 1 stopped part of the way, 2 refused."""
    parser = argparse.ArgumentParser(prog="python -m trace_lab.sync_push",
                                     description="Push this Trace Lab's eval store to the database in DEV_DB_URL.")
    parser.add_argument("--dry-run", action="store_true", help="count what would be pushed and write nothing")
    parser.add_argument("--full", action="store_true", help="read every row, not only those since the last push")
    args = parser.parse_args(argv)

    async def run() -> dict[str, Any]:
        try:
            return await push(dry_run=args.dry_run, full=args.full)
        finally:
            await db.dispose_engines()

    try:
        dest_url()
        out = asyncio.run(run())
    except Refused as refused:
        print(refused.error, file=sys.stderr)
        return 2
    print(summary_line(out))
    return 0 if out["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
