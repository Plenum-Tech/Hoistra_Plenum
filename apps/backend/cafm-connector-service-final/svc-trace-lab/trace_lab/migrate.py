"""Trace Lab's schema: numbered SQL files, a ledger, and a read-only verify at boot (G0 addendum 2.3).

Trace Lab keeps its data in its OWN database (TRACE_LAB_DB_URL), schema tracelab, never in the
application database. Every file in svc-trace-lab/migrations/ is named NNNN_name.sql, applied once,
recorded in tracelab.schema_migrations with its checksum, and never edited after it has been
applied to a shared database (SHIPPED_CHECKSUMS). Boot only READS: the ledger against the files,
then the live catalog against catalog.json. DDL runs only through apply_pending: at boot with
TRACE_LAB_SCHEMA=apply on a database named in TRACE_LAB_APPLY_DATABASES (never an Azure host),
or by Hussain through the CLI in an interactive terminal.

split_statements and the lock-timeout retry are ports of svc-operations-intelligence src/db.py
(_split_sql, _run_ddl_statement). This module imports no application code.

CLI (python -m trace_lab.migrate, run from svc-trace-lab with TRACE_LAB_DB_URL set):
  status                     verify and print the state (read-only)
  plan                       host, database, ledger state, pending files and their statements,
                             open jobs (read-only)
  apply --expect-db NAME --through NNNN --applied-by NAME [--dry-run]
                             interactive terminal only; asks for the database name to be typed
  snapshot --db-url URL      apply every file to a scratch database and write catalog.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.engine import make_url

log = structlog.get_logger(__name__)

SCHEMA = "tracelab"
MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
CATALOG_FILE = "catalog.json"
FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
LOCK_TIMEOUT = "3s"
STATEMENT_TIMEOUT = "60s"
VERIFY_STATEMENT_TIMEOUT = "15s"
FIRST_STATEMENT = f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"
ADVISORY_LOCK_KEY = 7310152026
LOCK_RETRIES = 4
LOCK_RETRY_SLEEP_S = 3.0
STATUSES = ("ready", "absent", "behind", "ahead", "drifted", "unreadable")
DEFAULT_APPLY_DATABASES = "tracelab_scratch"
SCRATCH_DATABASES = frozenset({"tracelab_scratch"})
SCRATCH_HOSTS = frozenset({"localhost", "127.0.0.1", "host.docker.internal"})

#: Checksums of the files applied to a shared Trace Lab database. Append only: a pinned file is
#: never edited (tests/test_trace_lab_migrate.py::test_shipped_files_are_never_edited); a
#: correction is a new file. After Hussain applies a file, the CLI prints the line to add here.
SHIPPED_CHECKSUMS: dict[int, str] = {
    # 0001-0009 applied to hoist_tracelab on Azure (plenum-agentic-ai) by Bala when Trace Lab was first deployed, 21 Oct 2026.
    1: "5e8edee750541f1e7b41a663b2895c81b8a68321826ae907438fedfc6c536df3",  # 0001_foundation.sql
    2: "f9750e7e4292448d4bd97e28219740569b6024131ee5ce2763d87546549bedd2",  # 0002_golden.sql
    3: "d0dc2fcf45d07ec0e9902b3b41a6167362e672baf546b386b0d7202531585436",  # 0003_review.sql
    4: "70cfd9c9e6109794bbbae3d4708d2d2ba6cc5db7d58658ab47bc0e59d7d215a0",  # 0004_taxonomy.sql
    5: "66fed96f7f0e05d7d8c65d9b81f238dea1de069a87a30bc331798cd56bd410c2",  # 0005_judges.sql
    6: "c5967b4791bd6c28d16e539b2edb882e05394bcd5ec3622eeb215f23957f4bce",  # 0006_watch.sql
    7: "9145541f5caa2c058dbc638db0e52197e45a2f63e0fd554b272c180396912938",  # 0007_judge_floor.sql
    8: "7d2ede6d043ebe28ad088077bec812fe56e141c17b0eefe9772d8ecc6db716c6",  # 0008_step_labels.sql
    9: "56d1a49b40e2639a57358d4c0e38a598e4d5809100da9e4a15d3819181c5ddd2",  # 0009_label_quotes.sql
    10: "cbb9c34a2d63da1841c26056b27583141a93ceb245bb954d289d063b6897d0d3",  # 0010_label_groups.sql, applied to hoist_tracelab by cli:hussain
    11: "caaeedb02d0b64b432346d681096a64de2a42977d56688082853385686764fce",  # 0011_runs.sql, applied to hoist_tracelab by cli:hussain
}


class MigrationFilesError(RuntimeError):
    """The migrations directory, or catalog.json, is not usable."""


@dataclass(frozen=True)
class MigrationFile:
    version: int
    name: str
    path: Path
    checksum: str
    sql: str


def iso(dt: datetime) -> str:
    """2026-10-15T09:12:03Z: the form every Trace Lab response uses for a check time."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_iso() -> str:
    return iso(datetime.now(timezone.utc))


@dataclass
class SchemaState:
    status: str                       # one of STATUSES
    expected_version: int
    applied_version: int | None
    pending: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    checked_at: str = field(default_factory=_now_iso)

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "expected_version": self.expected_version,
                "applied_version": self.applied_version, "pending": list(self.pending),
                "problems": list(self.problems), "checked_at": self.checked_at}


# ── files ─────────────────────────────────────────────────────────────────────────────────

def _normalise(raw: bytes) -> bytes:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def checksum(text: str | bytes) -> str:
    """sha256 hex of the file after dropping a UTF-8 BOM and turning CRLF or CR into LF."""
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    return hashlib.sha256(_normalise(raw)).hexdigest()


def files(directory: Path | None = None) -> list[MigrationFile]:
    """Every NNNN_name.sql in the directory, in order. Versions start at 0001 with no gap."""
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    if not d.is_dir():
        raise MigrationFilesError(f"no migrations directory at {d}")
    found: list[MigrationFile] = []
    seen: dict[int, str] = {}
    for p in sorted(d.iterdir()):
        if p.suffix != ".sql" or p.name.startswith("."):
            continue
        m = FILE_RE.match(p.name)
        if not m:
            raise MigrationFilesError(f"{p.name}: not a migration file name (NNNN_name.sql)")
        version = int(m.group(1))
        if version in seen:
            raise MigrationFilesError(f"{p.name}: version {version:04d} is also {seen[version]}")
        seen[version] = p.name
        raw = p.read_bytes()
        found.append(MigrationFile(version=version, name=p.name, path=p, checksum=checksum(raw),
                                   sql=_normalise(raw).decode("utf-8")))
    found.sort(key=lambda f: f.version)
    for i, f in enumerate(found, start=1):
        if f.version != i:
            raise MigrationFilesError(f"{f.name}: expected version {i:04d}; versions start at 0001 with no gaps")
    return found


_DOLLAR_TAG = re.compile(r"\$[A-Za-z0-9_]*\$")


def split_statements(sql: str) -> list[str]:
    """Statements on top-level ';' (port of svc-operations-intelligence src/db.py _split_sql).

    Dollar-quote aware: a ';' inside a $tag$ ... $tag$ body does not end a statement. Full-line
    '--' comments are dropped only outside a dollar-quoted body. A statement ends with ';' at the
    end of a line; the trailing ';' is removed."""
    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    for line in sql.splitlines():
        if dollar_tag is None and line.strip().startswith("--"):
            continue
        buf.append(line)
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            if dollar_tag is None:
                dollar_tag = tag
            elif tag == dollar_tag:
                dollar_tag = None
            pos = m.end()
        if dollar_tag is None and line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            buf = []
    tail = "\n".join(buf).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)
    return statements


_FORBIDDEN = (
    (re.compile(r"\bGRANT\b", re.I), "GRANT is DBA work"),
    (re.compile(r"\bREVOKE\b", re.I), "REVOKE is DBA work"),
    (re.compile(r"\bCONCURRENTLY\b", re.I), "CONCURRENTLY: every table is new"),
    (re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.I), "IF NOT EXISTS: a versioned file must fail loudly"),
    (re.compile(r"\b(?:plenum_cafm|plenum_scoped|plenum_eval|plenum_tracelab|public)\s*\.", re.I),
     "names another schema"),
)
_TARGETS = (
    re.compile(r"^CREATE\s+SCHEMA\s+(\S+)", re.I),
    re.compile(r"^COMMENT\s+ON\s+SCHEMA\s+(\S+)", re.I),
    re.compile(r"^CREATE\s+(?:UNLOGGED\s+)?TABLE\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+\S+\s+ON\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([^\s(]+)", re.I),
    re.compile(r"^CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+\S+\s+.*?\bON\s+([^\s(]+)", re.I | re.S),
    re.compile(r"^ALTER\s+(?:TABLE|INDEX|FUNCTION)\s+(?:ONLY\s+)?([^\s(]+)", re.I),
    re.compile(r"^DROP\s+(?:TABLE|INDEX|FUNCTION)\s+([^\s(]+)", re.I),
    re.compile(r"^DROP\s+TRIGGER\s+\S+\s+ON\s+([^\s(]+)", re.I),
    re.compile(r"^COMMENT\s+ON\s+(?:TABLE|COLUMN|FUNCTION|INDEX|CONSTRAINT\s+\S+\s+ON)\s+([^\s(]+)", re.I),
    re.compile(r"^(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+([^\s(]+)", re.I),
)
_REFERENCES = re.compile(r"\bREFERENCES\s+([^\s(]+)", re.I)
_COMMENT_LITERAL = re.compile(r"^COMMENT\s+ON\b.*?\bIS\s+'(.*)'$", re.I | re.S)


def lint(file: MigrationFile) -> list[str]:
    """The file rules: lock_timeout first; objects only in schema tracelab; no GRANT, REVOKE,
    CONCURRENTLY or IF NOT EXISTS; nothing after a ';' on its line; one-line comment strings
    without apostrophes. An empty list means clean."""
    problems: list[str] = []
    stmts = split_statements(file.sql)
    if not stmts or stmts[0] != FIRST_STATEMENT:
        problems.append(f"{file.name}: the first statement must be exactly {FIRST_STATEMENT};")
    dollar: str | None = None
    for n, line in enumerate(file.sql.splitlines(), start=1):
        if dollar is None and line.strip().startswith("--"):
            continue
        stripped = line.rstrip()
        if dollar is None and ";" in stripped and stripped.find(";") != len(stripped) - 1:
            problems.append(f"{file.name}:{n}: nothing may follow a ';' on its line")
        pos = 0
        while (m := _DOLLAR_TAG.search(line, pos)) is not None:
            tag = m.group(0)
            dollar = tag if dollar is None else (None if tag == dollar else dollar)
            pos = m.end()
    for i, s in enumerate(stmts, start=1):
        if re.match(r"^SET\s+LOCAL\s+", s, re.I):
            continue
        for rx, why in _FORBIDDEN:
            if rx.search(s):
                problems.append(f"{file.name}: statement {i}: {why}")
        target = None
        for rx in _TARGETS:
            m = rx.match(s)
            if m:
                target = m.group(1)
                break
        if target is None:
            problems.append(f"{file.name}: statement {i}: not a statement this check understands "
                            f"({' '.join(s.split())[:60]})")
        elif re.match(r"^(?:CREATE|COMMENT\s+ON)\s+SCHEMA\b", s, re.I):
            if target != SCHEMA:
                problems.append(f"{file.name}: statement {i}: only schema {SCHEMA} may be created")
        elif not target.startswith(SCHEMA + "."):
            problems.append(f"{file.name}: statement {i}: {target} is outside {SCHEMA}")
        for ref in _REFERENCES.findall(s):
            if not ref.startswith(SCHEMA + "."):
                problems.append(f"{file.name}: statement {i}: references {ref} outside {SCHEMA}")
        if re.match(r"^COMMENT\s+ON\b", s, re.I):
            lit = _COMMENT_LITERAL.match(s)
            if lit is None or "'" in lit.group(1) or "\n" in lit.group(1):
                problems.append(f"{file.name}: statement {i}: a comment string is one line with no apostrophes")
    return problems


# ── catalog comparison ────────────────────────────────────────────────────────────────────

def _ws(s: Any) -> str:
    return " ".join(str(s or "").split())


def compare_catalog(expected: dict, live: dict, *, same_major: bool = True) -> tuple[list[str], list[dict[str, str]]]:
    """Problems that make the schema 'drifted', and definition texts that differ only because the
    live Postgres major version formats them differently (logged, not failed)."""
    problems: list[str] = []
    differs: list[dict[str, str]] = []

    def text_diff(obj: str, exp: Any, got: Any) -> None:
        if _ws(exp) == _ws(got):
            return
        if same_major:
            problems.append(f"{obj}: definition changed")
        else:
            differs.append({"object": obj, "expected": _ws(exp)[:300], "found": _ws(got)[:300]})

    et, lt = set(expected.get("tables") or []), set(live.get("tables") or [])
    problems += [f"table {t}: missing" for t in sorted(et - lt)]
    problems += [f"table {t}: extra" for t in sorted(lt - et)]
    ec, lc = expected.get("columns") or {}, live.get("columns") or {}
    for t in sorted(et & lt):
        e_cols, l_cols = ec.get(t) or {}, lc.get(t) or {}
        problems += [f"{t}: missing column {c}" for c in sorted(set(e_cols) - set(l_cols))]
        problems += [f"{t}: extra column {c}" for c in sorted(set(l_cols) - set(e_cols))]
        for c in sorted(set(e_cols) & set(l_cols)):
            e, got = e_cols[c], l_cols[c]
            for k in ("type", "notnull", "identity"):
                if e.get(k) != got.get(k):
                    problems.append(f"{t}.{c}: {k} {e.get(k)!r} expected, {got.get(k)!r} found")
            text_diff(f"{t}.{c} default", e.get("default"), got.get("default"))
    for kind in ("indexes", "constraints", "triggers", "functions"):
        e_map, l_map = expected.get(kind) or {}, live.get(kind) or {}
        problems += [f"{name}: missing" for name in sorted(set(e_map) - set(l_map))]
        problems += [f"{name}: extra" for name in sorted(set(l_map) - set(e_map))]
        for name in sorted(set(e_map) & set(l_map)):
            e, got = e_map[name], l_map[name]
            if kind == "indexes":
                if e["table"] != got["table"]:
                    problems.append(f"{name}: on {got['table']}, expected {e['table']}")
                text_diff(name, e["def"], got["def"])
            elif kind == "constraints":
                if e["type"] != got["type"]:
                    problems.append(f"{name}: type {got['type']}, expected {e['type']}")
                text_diff(name, e["def"], got["def"])
            elif kind == "triggers":
                text_diff(name, e, got)
            elif e != got:   # functions: md5 of our own source text, never reformatted by Postgres
                problems.append(f"{name}: function body changed")
    return problems, differs


# ── boot-time apply guard and the scratch guard ───────────────────────────────────────────

def apply_refusal(database: str, host: str) -> str | None:
    """Why boot may not apply migrations here, or None. Never on an Azure host (the same test
    svc-deepagents' src/database.py uses for TLS), and only on a database named in
    TRACE_LAB_APPLY_DATABASES (default: tracelab_scratch only). host may be url_host's
    comma-joined candidate list (each with an optional :port); any Azure candidate refuses."""
    for h in (host or "").lower().split(","):
        h = re.sub(r":\d+$", "", h.strip())
        if h.endswith(".postgres.database.azure.com") or ".azure." in h:
            return "azure_host"
    allowed = {x.strip() for x in (os.getenv("TRACE_LAB_APPLY_DATABASES") or DEFAULT_APPLY_DATABASES).split(",")
               if x.strip()}
    if (database or "") not in allowed:
        return "not_in_allow_list"
    return None


def apply_allowed(database: str, host: str) -> bool:
    return apply_refusal(database, host) is None


# Query parameters that make a driver connect somewhere other than the URL's host and path
# (SQLAlchemy's asyncpg dialect lets ?host= and ?database= override both, and hands ?dsn= to asyncpg,
# which connects to the dsn's host when the URL names none).
CONNECTION_OVERRIDE_KEYS = frozenset({"host", "hostaddr", "database", "dbname", "service", "dsn"})


def _parse(url: str):
    """The URL the way the engine reads it (sqlalchemy.engine.make_url), not the way urlparse
    does: make_url does not split off a '#' fragment, so '...#?host=x' carries a real ?host=x
    query for the driver while urlparse sees only a fragment. None when it cannot be parsed."""
    try:
        return make_url(url) if url else None
    except Exception:
        return None


_OVERRIDE_TEXT_RE = re.compile(r"[?&#;](?:%s)\s*=" % "|".join(sorted(CONNECTION_OVERRIDE_KEYS)), re.IGNORECASE)


def _override_params(url: str) -> dict[str, tuple[str, ...]]:
    """Every value of every override key (lower-cased key), in URL order. A repeated ?host= is a
    multi-host list the driver tries in order, so no value is dropped."""
    u = _parse(url)
    if u is None:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for k, v in u.query.items():
        if k.lower() in CONNECTION_OVERRIDE_KEYS:
            vals = tuple(v) if isinstance(v, (tuple, list)) else (v,)
            out[k.lower()] = out.get(k.lower(), ()) + vals
    return out


def _host_candidates(url: str, depth: int = 0) -> list[str]:
    u = _parse(url)
    if u is None:
        return []
    over = _override_params(url)
    out = [*over.get("host", ()), *over.get("hostaddr", ())]
    for dsn in over.get("dsn", ()):
        inner = _host_candidates(dsn, depth + 1) if depth < 3 else []
        # an unparseable dsn still names its host after the last '@'; never echo the credentials
        out.extend(inner or [dsn.rsplit("@", 1)[-1]])
    out.append(u.host or "")
    return out


def url_host(url: str) -> str:
    """Every host a driver may really connect to, so the boot apply guard fails closed: all ?host=
    and ?hostaddr= values (a repeated ?host= is a multi-host list asyncpg tries in order), the
    host of any ?dsn= (read the same way), then the authority host, wherever the query sits in the
    string (including after a '#'). Lower-cased, de-duplicated, comma-joined, values keep any
    ':port'. With no host at all the driver reads PGHOST, so this does too; '' when that is unset."""
    seen: list[str] = []
    for part in _host_candidates(url):
        for h in part.lower().split(","):
            h = h.strip()
            if h and h not in seen:
                seen.append(h)
    return ",".join(seen) or (os.getenv("PGHOST") or "").strip().lower()


def scratch_url_ok(url: str) -> bool:
    """A throwaway database the SQL tests and `snapshot` may drop schemas in. A URL that carries a
    host, hostaddr, database, dbname, service or dsn parameter, or any '#', is refused (make_url
    drops a blank value, so the raw text is scanned too), because the driver would connect to that
    instead of the host and database this check read."""
    u = _parse(url)
    if u is None or "#" in url or _override_params(url) or _OVERRIDE_TEXT_RE.search(url):
        return False
    return (u.host or "").lower() in SCRATCH_HOSTS and (u.database or "") in SCRATCH_DATABASES


# ── reading the database ──────────────────────────────────────────────────────────────────

async def read_ledger(conn) -> list[dict] | None:
    """The applied files, oldest first; None when there is no ledger table."""
    present = (await conn.execute(sa_text("SELECT to_regclass('tracelab.schema_migrations') IS NOT NULL"))).scalar()
    if not present:
        return None
    rows = (await conn.execute(sa_text(
        "SELECT version, checksum, applied_at, applied_by FROM tracelab.schema_migrations ORDER BY version"
    ))).mappings().all()
    return [dict(r) for r in rows]


_TABLES_SQL = """
SELECT c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = :s AND c.relkind IN ('r', 'p') ORDER BY 1"""
_COLUMNS_SQL = """
SELECT c.relname AS tbl, a.attname AS col, format_type(a.atttypid, a.atttypmod) AS type,
       a.attnotnull AS notnull, pg_get_expr(d.adbin, d.adrelid) AS dflt, a.attidentity::text AS ident
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
  LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
 WHERE n.nspname = :s AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
 ORDER BY 1, 2"""
_INDEXES_SQL = """
SELECT i.relname AS name, t.relname AS tbl, pg_get_indexdef(i.oid) AS def
  FROM pg_index x
  JOIN pg_class i ON i.oid = x.indexrelid
  JOIN pg_class t ON t.oid = x.indrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s ORDER BY 1"""
_CONSTRAINTS_SQL = """
SELECT t.relname AS tbl, k.conname AS name, k.contype::text AS type, pg_get_constraintdef(k.oid) AS def
  FROM pg_constraint k
  JOIN pg_class t ON t.oid = k.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s AND k.contype IN ('c', 'p', 'u', 'f') ORDER BY 1, 2"""
_TRIGGERS_SQL = """
SELECT t.relname AS tbl, g.tgname AS name, pg_get_triggerdef(g.oid) AS def
  FROM pg_trigger g
  JOIN pg_class t ON t.oid = g.tgrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
 WHERE n.nspname = :s AND NOT g.tgisinternal ORDER BY 1, 2"""
_FUNCTIONS_SQL = """
SELECT p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' AS sig, md5(p.prosrc) AS src
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = :s ORDER BY 1"""


async def read_catalog(conn) -> dict:
    """The live catalog of schema tracelab, in the shape catalog.json stores."""
    p = {"s": SCHEMA}
    tables = [r[0] for r in (await conn.execute(sa_text(_TABLES_SQL), p)).all()]
    columns: dict[str, dict[str, dict[str, Any]]] = {}
    for r in (await conn.execute(sa_text(_COLUMNS_SQL), p)).mappings().all():
        columns.setdefault(r["tbl"], {})[r["col"]] = {
            "type": r["type"], "notnull": bool(r["notnull"]), "default": r["dflt"], "identity": r["ident"] or ""}
    indexes = {r["name"]: {"table": r["tbl"], "def": _ws(r["def"])}
               for r in (await conn.execute(sa_text(_INDEXES_SQL), p)).mappings().all()}
    constraints = {f"{r['tbl']}.{r['name']}": {"type": r["type"], "def": _ws(r["def"])}
                   for r in (await conn.execute(sa_text(_CONSTRAINTS_SQL), p)).mappings().all()}
    triggers = {f"{r['tbl']}.{r['name']}": _ws(r["def"])
                for r in (await conn.execute(sa_text(_TRIGGERS_SQL), p)).mappings().all()}
    functions = {r["sig"]: r["src"] for r in (await conn.execute(sa_text(_FUNCTIONS_SQL), p)).mappings().all()}
    return {"tables": tables, "columns": columns, "indexes": indexes, "constraints": constraints,
            "triggers": triggers, "functions": functions}


async def server_version_num(conn) -> int:
    return int((await conn.execute(sa_text("SELECT current_setting('server_version_num')::int"))).scalar() or 0)


def expected_catalog(directory: Path | None = None) -> dict:
    p = (Path(directory) if directory is not None else MIGRATIONS_DIR) / CATALOG_FILE
    if not p.is_file():
        raise MigrationFilesError(f"{CATALOG_FILE} is missing")
    return json.loads(p.read_text(encoding="utf-8"))


def error_state(problem: str, *, expected_version: int | None = None) -> SchemaState:
    """An 'unreadable' state: the check itself could not be made."""
    if expected_version is None:
        try:
            expected_version = len(files())
        except MigrationFilesError:
            expected_version = 0
    return SchemaState("unreadable", expected_version, None, problems=[problem[:300]])


# ── verify ────────────────────────────────────────────────────────────────────────────────

async def verify(engine, *, directory: Path | None = None) -> SchemaState:
    """Read-only: the ledger against the files, then the live catalog against catalog.json.
    Runs in a READ ONLY transaction, so Postgres itself refuses any write. Never raises."""
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    try:
        found = files(d)
    except MigrationFilesError as exc:
        return error_state(f"files: {exc}")
    expected = found[-1].version if found else 0
    try:
        async with engine.connect() as conn:
            await conn.execute(sa_text("SET TRANSACTION READ ONLY"))
            await conn.execute(sa_text(f"SET LOCAL statement_timeout = '{VERIFY_STATEMENT_TIMEOUT}'"))
            try:
                return await _verify_in(conn, found, expected, d)
            finally:
                await conn.rollback()
    except Exception as exc:  # noqa: BLE001 - a failed check is a state, never a crash
        return error_state(f"check failed: {str(exc)[:280]}", expected_version=expected)


async def _verify_in(conn, found: list[MigrationFile], expected: int, directory: Path) -> SchemaState:
    exists = (await conn.execute(sa_text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :s)"),
                                 {"s": SCHEMA})).scalar()
    all_versions = [f.version for f in found]
    if not exists:
        return SchemaState("absent", expected, None, pending=all_versions)
    ledger = await read_ledger(conn)
    if ledger is None:
        return SchemaState("drifted", expected, None, problems=["the schema exists but has no ledger"])
    by_version = {f.version: f for f in found}
    applied = [int(r["version"]) for r in ledger]
    top = max(applied) if applied else None
    ahead = [v for v in applied if v not in by_version]
    if ahead:
        return SchemaState("ahead", expected, top,
                           problems=[f"{v:04d}: applied, but this image has no such file" for v in ahead])
    mismatched = [f"{int(r['version']):04d}: checksum differs from {by_version[int(r['version'])].name}"
                  for r in ledger if r["checksum"] != by_version[int(r["version"])].checksum]
    if mismatched:
        return SchemaState("drifted", expected, top, problems=mismatched)
    if applied != list(range(1, len(applied) + 1)):
        return SchemaState("drifted", expected, top,
                           problems=["the ledger has a gap: " + ", ".join(f"{v:04d}" for v in applied)])
    pending = [v for v in all_versions if v not in applied]
    if pending:
        return SchemaState("behind", expected, top, pending=pending)
    try:
        want = expected_catalog(directory)
    except (MigrationFilesError, ValueError) as exc:
        return SchemaState("unreadable", expected, top, problems=[str(exc)[:300]])
    if int(want.get("version") or 0) != expected:
        return SchemaState("unreadable", expected, top,
                           problems=[f"{CATALOG_FILE} is for version {want.get('version')}, the files end at {expected}"])
    live = await read_catalog(conn)
    server = await server_version_num(conn)
    same_major = server // 10000 == int(want.get("server_version_num") or 0) // 10000
    problems, differs = compare_catalog(want, live, same_major=same_major)
    for diff in differs:
        log.warning("trace_lab.schema.definition_differs", **diff)
    if problems:
        return SchemaState("drifted", expected, top, problems=problems[:50])
    return SchemaState("ready", expected, top)


# ── apply ─────────────────────────────────────────────────────────────────────────────────

_LEDGER_INSERT = ("INSERT INTO tracelab.schema_migrations (version, checksum, applied_by)"
                  " VALUES (:v, :c, :b)")


def _is_lock_timeout(exc: BaseException) -> bool:
    t = str(exc).lower()
    return "lock timeout" in t or "lock_timeout" in t or "locknotavailable" in t or "55p03" in t


def _first_line(exc: BaseException) -> str:
    s = str(exc)
    return s.splitlines()[0][:200] if s else type(exc).__name__


async def _apply_one(engine, f: MigrationFile, applied_by: str) -> str | None:
    """One file in one transaction. None on success, else the problem to report."""
    stmts = split_statements(f.sql)
    log.info("trace_lab.schema.applying", version=f.version, name=f.name, checksum=f.checksum)
    for attempt in range(1, LOCK_RETRIES + 1):
        current = "(transaction setup)"
        t0 = time.perf_counter()
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                await conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
                await conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})")
                ledger = await read_ledger(conn) or []
                if any(int(r["version"]) == f.version for r in ledger):
                    log.info("trace_lab.schema.already_applied", version=f.version)
                    return None
                for current in stmts:
                    await conn.exec_driver_sql(current)
                await conn.execute(sa_text(_LEDGER_INSERT), {"v": f.version, "c": f.checksum, "b": applied_by})
            log.info("trace_lab.schema.applied", version=f.version, name=f.name, statements=len(stmts),
                     ms=round((time.perf_counter() - t0) * 1000))
            return None
        except Exception as exc:  # noqa: BLE001
            if _is_lock_timeout(exc) and attempt < LOCK_RETRIES:
                log.warning("trace_lab.schema.apply_lock_wait", version=f.version, attempt=attempt,
                            retry_in_s=LOCK_RETRY_SLEEP_S)
                await asyncio.sleep(LOCK_RETRY_SLEEP_S)
                continue
            snippet = " ".join(current.split())[:160]
            if _is_lock_timeout(exc):
                problem = f"{f.version:04d}: could not get its lock after {attempt} attempts: {snippet}"
            else:
                problem = f"{f.version:04d}: failed at: {snippet}: {_first_line(exc)}"
            log.error("trace_lab.schema.apply_failed", version=f.version, error=problem[:300])
            return problem
    return f"{f.version:04d}: not applied"


async def _dry_run(engine, todo: list[MigrationFile], applied_by: str) -> str | None:
    """Every selected file in one transaction, then rolled back. None when all of it ran."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            await conn.exec_driver_sql(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
            await conn.exec_driver_sql(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
            await conn.exec_driver_sql(f"SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY})")
            for f in todo:
                current = "(start)"
                try:
                    for current in split_statements(f.sql):
                        await conn.exec_driver_sql(current)
                    await conn.execute(sa_text(_LEDGER_INSERT), {"v": f.version, "c": f.checksum, "b": applied_by})
                except Exception as exc:  # noqa: BLE001
                    return f"{f.version:04d}: dry run failed at: {' '.join(current.split())[:160]}: {_first_line(exc)}"
            return None
        finally:
            await trans.rollback()


async def apply_pending(engine, *, applied_by: str, through: int | None = None, dry_run: bool = False,
                        directory: Path | None = None) -> SchemaState:
    """Apply the pending files, oldest first, each in its own transaction; then verify again.

    Runs only when the schema is absent or behind. It does not check TRACE_LAB_APPLY_DATABASES:
    boot does that before calling it, and the CLI has its own gates (a terminal, --expect-db,
    --through and the typed database name). A file that fails leaves nothing behind and the
    result is 'unreadable' with the problem first."""
    if not (1 <= len(applied_by or "") <= 200):
        raise ValueError("applied_by must be 1-200 characters")
    d = Path(directory) if directory is not None else MIGRATIONS_DIR
    state = await verify(engine, directory=d)
    if state.status not in ("absent", "behind"):
        if state.status != "ready":
            log.warning("trace_lab.schema.apply_refused", reason=f"status_{state.status}")
        return state
    by_version = {f.version: f for f in files(d)}
    todo = [by_version[v] for v in state.pending if through is None or v <= through]
    if not todo:
        return state
    if dry_run:
        problem = await _dry_run(engine, todo, applied_by)
        after = await verify(engine, directory=d)
        if problem:
            after.problems = [problem, *after.problems][:50]
        return after
    for f in todo:
        problem = await _apply_one(engine, f, applied_by)
        if problem:
            after = await verify(engine, directory=d)
            return SchemaState("unreadable", after.expected_version, after.applied_version, pending=after.pending,
                               problems=[problem, *after.problems][:50])
    return await verify(engine, directory=d)


async def current_database(engine) -> str:
    async with engine.connect() as conn:
        return str((await conn.execute(sa_text("SELECT current_database()"))).scalar())


def db_host(url: str | None = None) -> str:
    """Every host a driver may connect to for url (url_host: all ?host=/?hostaddr= values, a ?dsn='s host, the
    authority, PGHOST when none; comma-joined), or for TRACE_LAB_DB_URL from the environment when url is None.
    apply_refusal trusts this, so it must never return one guess."""
    if url is None:
        from .settings import load

        url = load().lab_db_url
    return url_host(url)


# ── CLI (never run by a Claude session against a shared database) ─────────────────────────

def _engine():
    """Trace Lab's own database, from TRACE_LAB_DB_URL (the CLI never installs the main engine)."""
    from . import db

    return db.lab_engine()


def _version_arg(v: str) -> int:
    if not re.fullmatch(r"\d{1,4}", v or ""):
        raise argparse.ArgumentTypeError("a version like 0002")
    return int(v)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m trace_lab.migrate", description="Trace Lab schema migrations.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="verify (read-only) and print the state")
    sub.add_parser("plan", help="print what apply would run (read-only)")
    a = sub.add_parser("apply", help="apply pending files: interactive terminal only")
    a.add_argument("--expect-db", required=True, help="must equal current_database()")
    a.add_argument("--through", required=True, type=_version_arg, help="the last version to apply, e.g. 0001")
    a.add_argument("--applied-by", required=True, help="recorded in the ledger as cli:<value>")
    a.add_argument("--dry-run", action="store_true", help="run the files in a transaction and roll back")
    s = sub.add_parser("snapshot", help="apply every file to a scratch database and write catalog.json")
    s.add_argument("--db-url", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cmd == "apply" and not sys.stdin.isatty():
        print("migrate apply runs only in an interactive terminal: a person types the database name to approve it.",
              file=sys.stderr)
        return 2
    if args.cmd == "snapshot" and not scratch_url_ok(args.db_url):
        print("snapshot runs only against a scratch database (tracelab_scratch on localhost, "
              "127.0.0.1 or host.docker.internal).", file=sys.stderr)
        return 2
    return asyncio.run(_run_cli(args))


async def _run_cli(args: argparse.Namespace) -> int:
    if args.cmd == "snapshot":
        return await _cli_snapshot(args.db_url)
    eng = _engine()
    try:
        if args.cmd == "status":
            st = await verify(eng)
            print(json.dumps(st.as_dict(), indent=2))
            return 0 if st.status == "ready" else 1
        if args.cmd == "plan":
            await _cli_plan(eng)
            return 0
        return await _cli_apply(eng, args)
    finally:
        await eng.dispose()


async def _open_jobs(engine) -> list[dict]:
    async with engine.connect() as conn:
        present = (await conn.execute(sa_text("SELECT to_regclass('tracelab.jobs') IS NOT NULL"))).scalar()
        if not present:
            return []
        rows = (await conn.execute(sa_text(
            "SELECT id::text AS id, kind, status, scope_key FROM tracelab.jobs"
            " WHERE status IN ('queued', 'running', 'paused', 'interrupted') ORDER BY created_at"))).mappings().all()
    return [dict(r) for r in rows]


async def _cli_plan(engine, through: int | None = None) -> SchemaState:
    st = await verify(engine)
    print(f"host: {db_host() or '?'}  database: {await current_database(engine)}")
    print(f"status: {st.status}  applied: {st.applied_version}  files end at: {st.expected_version}")
    for problem in st.problems[:20]:
        print(f"  problem: {problem}")
    by_version = {f.version: f for f in files()}
    for v in st.pending:
        if through is not None and v > through:
            continue
        f = by_version[v]
        print(f"\npending {f.name}  sha256 {f.checksum}")
        for problem in lint(f):
            print(f"  LINT: {problem}")
        for i, stmt in enumerate(split_statements(f.sql), start=1):
            print(f"-- statement {i}\n{stmt};")
    for j in await _open_jobs(engine):
        print(f"open job: {j['id']} {j['kind']} {j['status']} {j['scope_key']}")
    return st


async def _cli_apply(engine, args: argparse.Namespace) -> int:
    dbname = await current_database(engine)
    if dbname != args.expect_db:
        print(f"refused: connected to {dbname!r}, not {args.expect_db!r}.", file=sys.stderr)
        return 2
    st = await _cli_plan(engine, through=args.through)
    if st.status not in ("absent", "behind"):
        print(f"refused: the status is {st.status}; apply runs only when the schema is absent or behind.",
              file=sys.stderr)
        return 2
    todo = [v for v in st.pending if v <= args.through]
    if not todo:
        print("nothing to apply through that version.")
        return 0
    verb = "dry-run" if args.dry_run else "apply"
    typed = input(f"\nType the database name ({dbname}) to {verb} {', '.join(f'{v:04d}' for v in todo)}: ").strip()
    if typed != dbname:
        print("not confirmed; nothing ran.", file=sys.stderr)
        return 1
    after = await apply_pending(engine, applied_by=f"cli:{args.applied_by}", through=args.through,
                                dry_run=args.dry_run)
    print(json.dumps(after.as_dict(), indent=2))
    if after.status == "unreadable":
        return 1
    if args.dry_run:
        # A failed rehearsal comes back 'absent' or 'behind' with the problem first, so the exit code
        # must follow the problems, not the status.
        return 1 if after.problems else 0
    by_version = {f.version: f for f in files()}
    print("\nAdd to SHIPPED_CHECKSUMS in svc-trace-lab/trace_lab/migrate.py:")
    for v in todo:
        print(f'    {v}: "{by_version[v].checksum}",  # {by_version[v].name}, applied to {dbname} by cli:{args.applied_by}')
    return 0 if after.status in ("ready", "behind") else 1


async def _cli_snapshot(url: str) -> int:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    eng = create_async_engine(url, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        found = files()
        head = found[-1].version if found else 0
        st = await apply_pending(eng, applied_by="snapshot")
        if st.applied_version != head:
            print(f"snapshot failed: {json.dumps(st.as_dict())}", file=sys.stderr)
            return 1
        async with eng.connect() as conn:
            cat = await read_catalog(conn)
            server = await server_version_num(conn)
        out = {"version": head, "server_version_num": server, **cat}
        (MIGRATIONS_DIR / CATALOG_FILE).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {MIGRATIONS_DIR / CATALOG_FILE}: version {head}, server {server}, {len(cat['tables'])} tables")
        return 0
    finally:
        await eng.dispose()


if __name__ == "__main__":
    sys.exit(main())
