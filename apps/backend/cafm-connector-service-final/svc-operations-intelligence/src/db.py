from __future__ import annotations

import hashlib
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .core.logging import get_logger
from .models.base import Base
from . import models as _models  # noqa: F401

log = get_logger(__name__)

engine = create_async_engine(
    settings.db_url or "postgresql+asyncpg://cafm:cafm@localhost:5432/cafm_connectors",
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    # A session left idle inside a transaction holds every lock it took. One did, for 180 s
    # at a time, once an hour (reports/cards.py) — and a migration's ALTER TABLE queued behind
    # it took the whole service's auth reads down with it. The code no longer does that; this
    # is the server-side guarantee that no future await inside a transaction can. The name
    # is so pg_stat_activity says who a backend belongs to instead of an empty string.
    connect_args={
        "server_settings": {
            "idle_in_transaction_session_timeout": "60000",
            "application_name": "svc-operations-intelligence",
        }
    },
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

#: How many times a failing migration is retried after create_all. Three covers a chain of
#: three dependent migrations; a loop that never terminates would hang startup, which is
#: worse than a named unapplied file.
_MAX_MIGRATION_PASSES = 3

#: A concurrent index build has to run outside a transaction, so it is routed differently.
# UNIQUE sits between CREATE and INDEX, so a pattern without it routes
# "CREATE UNIQUE INDEX CONCURRENTLY" into a transaction, where Postgres refuses it
# outright — the index is then reported as a skipped statement and never exists,
# which for a uniqueness constraint means the thing it was guarding is unguarded.
_CONCURRENT_INDEX = re.compile(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+CONCURRENTLY", re.IGNORECASE)

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


async def init_db() -> None:
    """Migrate, create the ORM tables, then re-run whatever the first pass could not apply.

    The two passes exist because the ordering is genuinely circular. Some migrations create
    things `create_all` needs — the schema itself, tables the ORM does not model — so they
    have to run first. Others ALTER tables the ORM owns, which on a fresh database do not
    exist until `create_all` has run, so those are skipped on the way past.

    Nobody noticed because the second boot fixed it: by then the tables existed and the
    ALTERs applied. A first-boot deployment ran with columns missing and failed with a 500
    that looked like a code fault — `compliance_certificates.site_ref does not exist` — until
    somebody restarted the service and it mysteriously healed.

    Only the files that actually failed are retried, so a healthy start still costs one pass.
    Every migration is written idempotent, which is what makes a second attempt safe.
    """
    log.info("db.init", status="starting")
    retry: dict[Path, str] = {}
    if settings.auto_migrate_on_startup:
        # Swept before the run, so an index left unfinished by a previous concurrent build
        # is rebuilt on this one rather than skipped forever by IF NOT EXISTS.
        swept = await _drop_invalid_indexes()
        if swept:
            log.warning("db.invalid_indexes_dropped", indexes=swept,
                        note="left unfinished by an earlier concurrent build; rebuilding")
        # An index swept away has to be rebuilt by the file that declares it, and the ledger
        # cannot say which file that is -- so a start that swept one runs everything, the way
        # every start did before the ledger.
        retry = await apply_sql_migrations(use_ledger=not swept)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Retried until nothing more succeeds, not just once. Filename order does not express
    # dependencies BETWEEN migrations: compliance_certificates_region.sql indexes a column
    # that compliance_certificates_state.sql adds, and `region` sorts before `state`, so in
    # any single pass that index can never build. Looping while the failure set shrinks
    # resolves those without anyone having to rename files into a working order — which is
    # a trap that re-opens every time a migration is added.
    passes = 0
    while retry and passes < _MAX_MIGRATION_PASSES:
        passes += 1
        log.info("db.init.migration_retry", pass_no=passes,
                 files=[f.name for f in retry])
        still_failing = await apply_sql_migrations(only=list(retry))
        if len(still_failing) >= len(retry):
            retry = still_failing
            break          # no progress: another pass will not help
        retry = still_failing
    if retry:
        # Converged and still failing. Some of these are genuinely fine — a migration for a
        # table another service owns will never apply here — but that has to be declared
        # per file, not assumed for all of them.
        allowed = {
            n.strip()
            for n in str(settings.migrations_allowed_to_fail or "").split(",")
            if n.strip()
        }
        permitted = {f: e for f, e in retry.items() if f.name in allowed}
        blocking = {f: e for f, e in retry.items() if f.name not in allowed}
        if permitted:
            log.warning(
                "db.init.migration_unapplied_permitted",
                files=[f.name for f in permitted],
                note="listed in MIGRATIONS_ALLOWED_TO_FAIL",
            )
        if blocking:
            detail = "; ".join(f"{f.name}: {e}" for f, e in blocking.items())
            log.error(
                "db.init.migration_failed",
                files=[f.name for f in blocking],
                errors={f.name: e for f, e in blocking.items()},
                note="refusing to start — the schema is half-applied",
            )
            raise MigrationError(
                f"{len(blocking)} migration(s) could not be applied after "
                f"{passes} retry pass(es): {detail}. Fix the migration, or name the file "
                f"in MIGRATIONS_ALLOWED_TO_FAIL if it belongs to another service."
            )
    log.info("db.init", status="complete")


class MigrationError(RuntimeError):
    """A migration could not be applied and the service must not serve traffic.

    Raised from init_db after the retry passes have converged. Starting anyway is how a
    half-applied schema reaches production looking healthy: the statements that succeeded
    stay, the ones that failed are a warning in a log nobody reads during a deploy, and
    the first request to touch a missing column returns a 500 that looks like a code fault.
    """


_DOLLAR_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")


def _split_sql(sql: str) -> list[str]:
    """Split a SQL script into statements on top-level ``;``.

    Dollar-quote aware: a ``;`` inside a ``$tag$ … $tag$`` block (PL/pgSQL function
    bodies, ``DO`` blocks) is NOT a statement terminator — otherwise the splitter shreds
    such blocks into fragments that each fail (append-only audit triggers, etc.). Full-line
    ``--`` comments are stripped only when outside a dollar-quoted block.
    """
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None  # e.g. "$fn$" while inside a dollar-quoted block

    for line in sql.splitlines():
        if dollar_tag is None and line.strip().startswith("--"):
            continue
        buf.append(line)
        # Toggle in/out of dollar-quoted blocks as tags open and close on this line.
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            if dollar_tag is None:
                dollar_tag = tag
            elif tag == dollar_tag:
                dollar_tag = None
            pos = m.end()
        # A ';' ends a statement only at top level (not inside a dollar-quoted block).
        if dollar_tag is None and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


async def exec_migration_statements(statements: list[str]) -> tuple[int, list[str]]:
    """Run migration statements, sending concurrent index builds outside a transaction.

    The single place that knows how to execute this kind of SQL. Three call sites applied
    migration files with ``engine.begin()`` of their own, and converting the index builds to
    CONCURRENTLY broke two of them at once — the SQL is shared, so the knowledge of how to
    run it has to be too, or the next change breaks them again.

    Returns ``(applied, errors)``. Each statement runs on its own so one failure cannot
    abort the rest.
    """
    applied = 0
    errors: list[str] = []
    for stmt in statements:
        if not str(stmt).strip():
            continue
        try:
            await _run_ddl_statement(stmt)
            applied += 1
        except Exception as exc:  # noqa: BLE001 — one bad statement must not lose the file
            errors.append(str(exc)[:300])
    return applied, errors


#: How long a migration statement may wait for a table lock before giving up and retrying.
#:
#: Caught live on 17 Sep 2026 at 08:07 UTC: `DO $uuid_default$ … ALTER TABLE plenum_cafm.users`
#: from auth_identity_and_otp.sql waited three minutes for an exclusive lock behind one
#: session that was idle in a transaction holding a share lock on users — and, because a
#: waiting exclusive lock blocks every NEW share lock behind it, every token check in the
#: service queued behind the ALTER. Twenty of them by the time the holder committed.
#:
#: These statements are idempotent and re-run on every start, so waiting is never worth it:
#: an ALTER that cannot get its lock in five seconds backs off and tries again rather than
#: turning the auth path of the running service into a queue.
_DDL_LOCK_TIMEOUT = "5s"
_DDL_LOCK_RETRIES = 4
_DDL_LOCK_RETRY_SLEEP_S = 3.0


def _is_lock_timeout(exc: BaseException) -> bool:
    text_ = str(exc).lower()
    return "lock timeout" in text_ or "lock_timeout" in text_ or "locknotavailable" in text_


_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
#: The plain form only. A `DO $$ … $$` block is not pre-checkable and runs under the lock timeout.
_ADD_COLUMN = re.compile(
    rf"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?:(?P<schema>{_IDENT})\.)?(?P<table>{_IDENT})\s+"
    rf"ADD\s+(?:COLUMN\s+)?IF\s+NOT\s+EXISTS\s+(?P<column>{_IDENT})\b",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_EXISTS_SQL = (
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_schema = COALESCE(:s, current_schema()) AND table_name = :t AND column_name = :c "
    "LIMIT 1"
)


def _parse_add_column(stmt: str) -> tuple[str | None, str, str] | None:
    m = _ADD_COLUMN.match(stmt or "")
    return (m.group("schema"), m.group("table"), m.group("column")) if m else None


async def _column_already_exists(stmt: str) -> bool:
    """True when `stmt` is a plain ADD COLUMN IF NOT EXISTS whose column is already there.

    `ADD COLUMN IF NOT EXISTS` takes ACCESS EXCLUSIVE before it discovers the column exists, so
    on a steady-state database every start paid the lock for nothing — and on 17 Sep 2026, with
    readers holding the tables, paid four lock timeouts per statement, thirteen files deep, for
    a start that took minutes and left the identity service on 502. Asking the catalogue costs a
    share lock on nothing. Any failure to ask counts as "not known to exist": the ALTER is the
    authority, this is only the shortcut.
    """
    parsed = _parse_add_column(stmt)
    if not parsed:
        return False
    from sqlalchemy import text as _text
    schema, table, column = parsed
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(_text(_COLUMN_EXISTS_SQL), {"s": schema, "t": table, "c": column})).scalar()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


async def _run_ddl_statement(stmt: str) -> None:
    """One migration statement, with a lock timeout and a short retry loop.

    Concurrent index builds run on the raw asyncpg connection outside any transaction
    (Postgres refuses them inside one); everything else runs in its own transaction with a
    ``SET LOCAL lock_timeout`` so a lock it cannot get promptly is an error to retry, not a
    wait that takes the rest of the service down with it.
    """
    import asyncio as _asyncio

    if await _column_already_exists(stmt):
        log.debug("db.migration.already_applied", statement=str(stmt).strip()[:120])
        return

    for attempt in range(1, _DDL_LOCK_RETRIES + 1):
        try:
            if _CONCURRENT_INDEX.search(stmt):
                async with engine.connect() as conn:
                    raw = await conn.get_raw_connection()
                    await raw.driver_connection.execute(f"SET lock_timeout = '{_DDL_LOCK_TIMEOUT}'")
                    try:
                        await raw.driver_connection.execute(stmt)
                    finally:
                        await raw.driver_connection.execute("RESET lock_timeout")
            else:
                async with engine.begin() as conn:
                    await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{_DDL_LOCK_TIMEOUT}'")
                    await conn.exec_driver_sql(stmt)
            return
        except Exception as exc:  # noqa: BLE001
            if _is_lock_timeout(exc) and attempt < _DDL_LOCK_RETRIES:
                log.warning(
                    "db.migration.lock_wait",
                    attempt=attempt,
                    retry_in_s=_DDL_LOCK_RETRY_SLEEP_S,
                    statement=str(stmt).strip()[:120],
                    note="a reader holds the table; backing off rather than queueing the service behind this",
                )
                await _asyncio.sleep(_DDL_LOCK_RETRY_SLEEP_S)
                continue
            raise


async def _drop_invalid_indexes() -> list[str]:
    """Remove indexes a concurrent build left behind unfinished.

    This is the trap that comes with CREATE INDEX CONCURRENTLY. When a concurrent build
    fails part-way, Postgres keeps the index in an INVALID state: it exists, so
    ``IF NOT EXISTS`` skips it on every later run, and no query will use it. The result is
    an index that is permanently missing while looking permanently present.

    Dropping one is safe by definition — an invalid index is unusable, so nothing can be
    relying on it — and it lets the next migration pass build it properly.
    """
    dropped: list[str] = []
    try:
        async with engine.connect() as conn:
            raw = await conn.get_raw_connection()
            rows = await raw.driver_connection.fetch(
                """SELECT c.relname AS name
                   FROM pg_index i
                   JOIN pg_class c ON c.oid = i.indexrelid
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'plenum_cafm' AND NOT i.indisvalid"""
            )
            for r in rows:
                name = r["name"]
                await raw.driver_connection.execute(
                    f'DROP INDEX IF EXISTS plenum_cafm."{name}"'
                )
                dropped.append(str(name))
    except Exception as exc:  # noqa: BLE001 — housekeeping must not stop startup
        log.warning("db.invalid_index_sweep_failed", error=str(exc)[:200])
    return dropped


async def apply_sql_migrations(
    only: list[Path] | None = None,
    use_ledger: bool = True,
) -> dict[Path, str]:
    """Apply the idempotent *.sql migrations in filename order.

    Returns {file: last error} for every file that had at least one statement fail, so the
    caller can retry them once the ORM tables exist — and, when they still fail, say why
    rather than just which. ``only`` restricts the run to a previous pass's failures.

    A file the ledger records as applied with its current statements is skipped outright: no
    statement runs, so no lock is taken. ``use_ledger=False`` (or MIGRATIONS_LEDGER=0) runs
    every file regardless, which is how every start behaved before 6 Nov 2026.
    """
    if not _MIGRATIONS_DIR.exists():
        log.warning("db.migration.dir_missing", path=str(_MIGRATIONS_DIR))
        return []
    files = list(only) if only is not None else sorted(_MIGRATIONS_DIR.glob("*.sql"))
    total = 0
    skipped = 0
    failed: dict[Path, str] = {}
    ledger = await _ledger_read() if use_ledger and settings.migrations_ledger else None
    for mig in files:
        source = mig.read_text(encoding="utf-8")
        statements = _split_sql(source)
        checksum = _statements_checksum(statements)
        every_start = bool(_EVERY_START.search(source))
        if ledger is not None and not every_start and ledger.get(mig.name) == checksum:
            # Applied before, with exactly these statements: running them again changes
            # nothing and takes the locks anyway.
            skipped += 1
            continue
        file_failed = False
        for stmt in statements:
            # Each statement in its OWN transaction: one bad statement (e.g. a
            # PL/pgSQL dollar-quoted block that _split_sql can't split cleanly) must
            # not abort the shared transaction and poison every later migration.
            # exec_driver_sql (raw DBAPI) — NOT text(): several seeds use PostgreSQL
            # ``::jsonb`` casts, which text() misreads as ``:jsonb`` bind params.
            try:
                # CREATE INDEX CONCURRENTLY runs outside a transaction on the raw asyncpg
                # connection (Postgres refuses it inside one, and the pooled engine's
                # AUTOCOMMIT option never took effect); everything else in its own
                # transaction. Both with a lock timeout — see _run_ddl_statement for the
                # three-minute outage that a single waiting ALTER TABLE caused.
                await _run_ddl_statement(stmt)
                total += 1
            except Exception as exc:  # noqa: BLE001
                # Tolerated HERE on purpose: within a pass, a statement can fail only
                # because the thing it needs has not been created yet, and the caller
                # retries. What must not be tolerated is a failure that survives the
                # retries — that is handled in init_db.
                failed[mig] = str(exc)[:300]
                file_failed = True
                log.warning(
                    "db.migration.stmt_skipped",
                    file=mig.name,
                    error=str(exc)[:300],
                )
        log.info("db.migration.file_applied", file=mig.name, statements=len(statements))
        # 'applied' only when EVERY statement succeeded. A file that failed part-way is recorded
        # as failed with its error, and -- not being applied -- runs again on the next start,
        # exactly as before the ledger.
        if ledger is not None:
            await _ledger_record(mig.name, checksum, len(statements),
                                 error=failed[mig] if file_failed else None)
    log.info("db.migration.applied", statements=total, files=len(files),
             files_with_failures=len(failed), skipped_by_ledger=skipped,
             ledger="off" if ledger is None else "on")
    return failed


#: A file that must run on every start says so on a line of its own. Only for files whose result
#: depends on what is in the database rather than on their own text -- udr_scoped_views.sql builds a
#: view per table from information_schema, so a table added since the last start needs it to run.
#: Such a file must then be cheap and lock-free when there is nothing to do; the ledger cannot
#: make it so.
_EVERY_START = re.compile(r"^--\s*ledger:\s*every-start\s*$", re.IGNORECASE | re.MULTILINE)

#: One row per migration file: what its last attempt did. `status` is 'applied' only when every
#: statement succeeded; 'failed' keeps the error, so whether a file is stuck -- and why -- can be
#: read live (GET /api/superadmin/migrations) instead of from a log that scrolled away.
_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS plenum_cafm.ops_schema_migrations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    filename        TEXT NOT NULL UNIQUE,
    checksum        TEXT NOT NULL,
    statements      INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'applied' CHECK (status IN ('applied', 'failed')),
    last_error      TEXT,
    applied_at      TIMESTAMPTZ,
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by      TEXT
)
"""


def _statements_checksum(statements: list[str]) -> str:
    """What the file would EXECUTE, not how it is written.

    Hashed after the splitter has dropped full-line comments and with whitespace collapsed, so
    a comment or a re-indent does not count as a change -- otherwise every edit to the prose
    these files carry would re-run them, locks and all. A changed statement does count.
    """
    h = hashlib.sha256()
    for stmt in statements:
        h.update(" ".join(stmt.split()).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


async def _ledger_read() -> dict[str, str] | None:
    """{filename: checksum} for every file applied before, or None to run everything.

    Why it exists (6 Nov 2026): every start re-ran all fifty files, and measured against a copy
    of hoistra_test's own schema that was 305 table locks -- 215 of them exclusive locks on the
    plenum_scoped views every scoped read goes through -- on a database that needed none of
    them. A reader holding any of those tables queued the start behind it, and a waiting
    exclusive lock queues every new reader behind IT. Three times that took the identity service
    down for a quarter of an hour (17 Sep, 6 Oct, 6 Nov), each time with someone else's long
    query holding plenum_cafm.users.

    Any failure here -- no permission to create the table, a database that refuses it -- falls
    back to running every file, which is what the service did before. The ledger is an
    optimisation of a run that is already idempotent; it is never a reason not to start.
    """
    from sqlalchemy import text as _text
    try:
        await _run_ddl_statement(_LEDGER_DDL)
        async with engine.connect() as conn:
            rows = (await conn.execute(_text(
                "SELECT filename, checksum FROM plenum_cafm.ops_schema_migrations"
                " WHERE status = 'applied'"))).all()
        return {r[0]: r[1] for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.warning("db.migration.ledger_unavailable", error=str(exc)[:200],
                    note="running every migration file, as before the ledger")
        return None


async def _ledger_record(filename: str, checksum: str, statements: int,
                         error: str | None = None) -> None:
    """Record a file's last attempt. A failure keeps the previous applied_at: 'it last worked
    at X, and has failed since' is more useful than losing when it last worked."""
    from sqlalchemy import text as _text
    ok = error is None
    try:
        async with engine.begin() as conn:
            await conn.execute(_text(
                "INSERT INTO plenum_cafm.ops_schema_migrations "
                "  (filename, checksum, statements, status, last_error, applied_at, applied_by) "
                "VALUES (:f, :c, :n, :s, :e, CASE WHEN :ok THEN now() END, "
                "        current_setting('application_name', true)) "
                "ON CONFLICT (filename) DO UPDATE SET checksum = EXCLUDED.checksum, "
                "  statements = EXCLUDED.statements, status = EXCLUDED.status, "
                "  last_error = EXCLUDED.last_error, last_attempt_at = now(), "
                "  applied_at = COALESCE(EXCLUDED.applied_at, plenum_cafm.ops_schema_migrations.applied_at), "
                "  applied_by = EXCLUDED.applied_by"),
                {"f": filename, "c": checksum, "n": statements,
                 "s": "applied" if ok else "failed", "e": None if ok else error[:1000], "ok": ok})
    except Exception as exc:  # noqa: BLE001 - an unrecorded file just runs again next start
        log.warning("db.migration.ledger_record_failed", file=filename, error=str(exc)[:200])


async def migration_status() -> dict:
    """Every migration file in this build, and where this database stands with it.

        applied      ran in full, with the statements the file has now; skipped on start
        changed      ran in full once, but its statements have changed since; runs next start
        failed       the last attempt failed; last_error says why; runs again next start
        pending      never recorded on this database; runs next start
        every-start  declared to run on every start (see _EVERY_START)
        removed      recorded in the ledger, no longer in this build

    Read-only: one SELECT on the ledger and the files on disk. The HTTP route over this answers
    only once the service is up; during a start, the ledger table itself shows progress, each
    file's row being written as that file finishes.
    """
    from sqlalchemy import text as _text
    rows: dict[str, dict] = {}
    ledger_error = None
    try:
        async with engine.connect() as conn:
            exists = (await conn.execute(_text(
                "SELECT to_regclass('plenum_cafm.ops_schema_migrations')"))).scalar()
            if exists:
                result = await conn.execute(_text(
                    "SELECT filename, checksum, statements, status, last_error, applied_at,"
                    "       last_attempt_at, applied_by"
                    "  FROM plenum_cafm.ops_schema_migrations"))
                rows = {r["filename"]: dict(r) for r in result.mappings()}
    except Exception as exc:  # noqa: BLE001
        ledger_error = str(exc)[:200]

    files = sorted(_MIGRATIONS_DIR.glob("*.sql")) if _MIGRATIONS_DIR.exists() else []
    out = []
    for f in files:
        source = f.read_text(encoding="utf-8")
        statements = _split_sql(source)
        checksum = _statements_checksum(statements)
        row = rows.pop(f.name, None)
        if _EVERY_START.search(source):
            state = "every-start"
        elif row is None:
            state = "pending"
        elif row["status"] == "failed":
            state = "failed"
        elif row["checksum"] != checksum:
            state = "changed"
        else:
            state = "applied"
        out.append({
            "file": f.name, "status": state, "statements": len(statements),
            "last_error": row["last_error"] if row else None,
            "applied_at": row["applied_at"].isoformat() if row and row["applied_at"] else None,
            "last_attempt_at": (row["last_attempt_at"].isoformat()
                                if row and row["last_attempt_at"] else None),
            "applied_by": row["applied_by"] if row else None,
        })
    for name, row in sorted(rows.items()):
        out.append({"file": name, "status": "removed", "statements": row["statements"],
                    "last_error": row["last_error"],
                    "applied_at": row["applied_at"].isoformat() if row["applied_at"] else None,
                    "last_attempt_at": (row["last_attempt_at"].isoformat()
                                        if row["last_attempt_at"] else None),
                    "applied_by": row["applied_by"]})
    counts: dict[str, int] = {}
    for item in out:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "ledger": "off" if not settings.migrations_ledger else "on",
        "ledger_error": ledger_error,
        "counts": counts,
        # What the NEXT start will run: everything not applied. Zero here means a restart
        # takes no migration locks at all, apart from the every-start files' own reads.
        "will_run_next_start": [i["file"] for i in out
                                if i["status"] in ("pending", "changed", "failed", "every-start")],
        "files": out,
    }


async def apply_sql_seed(name: str) -> int:
    """Apply one idempotent seed file from seeds/ (statement by statement). Returns applied count."""
    path = _SEEDS_DIR / name
    if not path.exists():
        log.warning("db.seed.missing", path=str(path))
        return 0
    applied = 0
    for stmt in _split_sql(path.read_text(encoding="utf-8")):
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(stmt)
            applied += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("db.seed.stmt_skipped", file=name, error=str(exc)[:300])
    log.info("db.seed.applied", file=name, statements=applied)
    return applied


async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
