"""Trace Lab migrations without a database: files, checksums, the splitter, the file rules, the
boot-time apply guard, the scratch guard and the catalog comparison (G0 addendum 2.3)."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from trace_lab import migrate

FUNC = """-- header comment
SET LOCAL lock_timeout = '3s';

CREATE FUNCTION tracelab.f() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- a comment inside the body stays
    RAISE EXCEPTION 'tracelab.% refused', TG_TABLE_NAME;
END
$$;
CREATE TABLE tracelab.t (id integer PRIMARY KEY);
"""


def _write(d: Path, name: str, body: str = "SET LOCAL lock_timeout = '3s';\n") -> None:
    (d / name).write_text(body, encoding="utf-8")


def test_the_schema_and_folder():
    assert migrate.SCHEMA == "tracelab"
    assert migrate.MIGRATIONS_DIR == Path(migrate.__file__).resolve().parents[1] / "migrations"
    assert migrate.STATUSES == ("ready", "absent", "behind", "ahead", "drifted", "unreadable")


def test_checksum_ignores_bom_and_line_endings():
    lf = migrate.checksum(b"SELECT 1;\nSELECT 2;\n")
    assert migrate.checksum(b"\xef\xbb\xbfSELECT 1;\r\nSELECT 2;\r\n") == lf
    assert migrate.checksum(b"SELECT 1;\rSELECT 2;\r") == lf
    assert migrate.checksum("SELECT 1;\nSELECT 2;\n") == lf           # text and bytes agree
    assert migrate.checksum(b"SELECT 1;\nSELECT 2;") != lf             # any other byte change is an edit
    assert len(lf) == 64 and lf == lf.lower()


def test_split_statements_keeps_dollar_bodies_whole():
    stmts = migrate.split_statements(FUNC)
    assert len(stmts) == 3
    assert stmts[0] == "SET LOCAL lock_timeout = '3s'"
    assert stmts[1].startswith("CREATE FUNCTION tracelab.f()") and stmts[1].endswith("$$")
    assert "-- a comment inside the body stays" in stmts[1] and "RAISE EXCEPTION" in stmts[1]
    assert stmts[2] == "CREATE TABLE tracelab.t (id integer PRIMARY KEY)"


def test_files_reads_versions_in_order_and_skips_the_catalog(tmp_path):
    _write(tmp_path, "0002_two.sql")
    _write(tmp_path, "0001_one.sql")
    (tmp_path / "catalog.json").write_text("{}", encoding="utf-8")
    found = migrate.files(tmp_path)
    assert [(f.version, f.name) for f in found] == [(1, "0001_one.sql"), (2, "0002_two.sql")]
    assert found[0].checksum == migrate.checksum(b"SET LOCAL lock_timeout = '3s';\n")


@pytest.mark.parametrize("names, why", [
    (["0001_one.sql", "0003_three.sql"], "expected version 0002"),
    (["0002_two.sql"], "expected version 0001"),
    (["0001_one.sql", "0001_again.sql"], "is also"),
    (["0001_One.sql"], "not a migration file name"),
    (["1_one.sql"], "not a migration file name"),
])
def test_files_refuses_gaps_duplicates_and_bad_names(tmp_path, names, why):
    for n in names:
        _write(tmp_path, n)
    with pytest.raises(migrate.MigrationFilesError, match=why):
        migrate.files(tmp_path)


def test_a_missing_directory_is_a_load_error(tmp_path):
    with pytest.raises(migrate.MigrationFilesError, match="no migrations directory"):
        migrate.files(tmp_path / "nope")


def test_lint_catches_grants_other_schemas_if_not_exists_and_late_text(tmp_path):
    body = ("SET LOCAL lock_timeout = '3s';\n"
            "GRANT SELECT ON tracelab.jobs TO someone;\n"
            "CREATE INDEX ix_bad ON public.agent_trace_turns (turn_id);\n"
            "CREATE TABLE IF NOT EXISTS tracelab.x (id integer);\n"
            "CREATE INDEX CONCURRENTLY ix_c ON tracelab.x (id);\n"
            "CREATE TABLE tracelab.y (id integer); SELECT 1;\n"
            "COMMENT ON TABLE tracelab.y IS 'it''s wrong';\n"
            "CREATE TABLE tracelab.z (id integer REFERENCES other.t (id));\n")
    _write(tmp_path, "0001_bad.sql", body)
    problems = "\n".join(migrate.lint(migrate.files(tmp_path)[0]))
    for needle in ("GRANT is DBA work", "names another schema", "IF NOT EXISTS", "CONCURRENTLY",
                   "nothing may follow a ';'", "no apostrophes", "references other.t outside tracelab",
                   "public.agent_trace_turns is outside tracelab"):
        assert needle in problems, needle


def test_lint_wants_the_lock_timeout_first(tmp_path):
    _write(tmp_path, "0001_x.sql", "CREATE TABLE tracelab.x (id integer);\n")
    assert any("first statement" in p for p in migrate.lint(migrate.files(tmp_path)[0]))


def test_lint_allows_only_the_tracelab_schema(tmp_path):
    _write(tmp_path, "0001_x.sql", "SET LOCAL lock_timeout = '3s';\nCREATE SCHEMA evaluation;\n")
    assert any("only schema tracelab may be created" in p for p in migrate.lint(migrate.files(tmp_path)[0]))


def test_lint_accepts_a_clean_file(tmp_path):
    _write(tmp_path, "0001_ok.sql", FUNC + "COMMENT ON TABLE tracelab.t IS 'A clean comment.';\n")
    assert migrate.lint(migrate.files(tmp_path)[0]) == []


@pytest.mark.parametrize("database, host, reason", [
    ("tracelab_scratch", "127.0.0.1", None),
    ("tracelab_scratch", "host.docker.internal", None),
    ("hoist_tracelab", "postgres", "not_in_allow_list"),
    ("tracelab_scratch", "plenum-agentic-ai.postgres.database.azure.com", "azure_host"),
    ("tracelab_scratch", "db.azure.example.net", "azure_host"),
    # url_host returns every candidate host, comma-joined; any Azure one refuses
    ("tracelab_scratch", "a.postgres.database.azure.com:5432,localhost:5432,localhost", "azure_host"),
    ("tracelab_scratch", "localhost,plenum.postgres.database.azure.com", "azure_host"),
])
def test_boot_apply_is_refused_off_the_allow_list_and_on_azure_hosts(monkeypatch, database, host, reason):
    monkeypatch.delenv("TRACE_LAB_APPLY_DATABASES", raising=False)
    assert migrate.apply_refusal(database, host) == reason
    assert migrate.apply_allowed(database, host) is (reason is None)


def test_the_apply_allow_list_is_configurable(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_APPLY_DATABASES", "hoist_tracelab_local, other")
    assert migrate.apply_allowed("hoist_tracelab_local", "localhost")
    assert not migrate.apply_allowed("tracelab_scratch", "localhost")


@pytest.mark.parametrize("url, ok", [
    ("postgresql+asyncpg://postgres:scratch@host.docker.internal:55432/tracelab_scratch", True),
    ("postgresql+asyncpg://postgres:scratch@127.0.0.1:55433/tracelab_scratch", True),
    ("postgresql+asyncpg://postgres:scratch@localhost:55432/tl_scratch", False),
    ("postgresql+asyncpg://u:p@localhost/hoist_tracelab", False),
    ("postgresql+asyncpg://u:p@plenum.postgres.database.azure.com/tracelab_scratch", False),
    ("", False),
    # query parameters override the host and database in the asyncpg dialect
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?host=evil.example.com", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?database=hoist_prod", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?dbname=hoist_prod", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?hostaddr=10.0.0.5", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?service=prod", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?HOST=evil.example.com", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?host=", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?sslmode=disable", True),
    # a '#' hides the query from urlparse but not from SQLAlchemy's make_url
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch#?host=prod.example.com", False),
    ("postgresql+asyncpg://postgres:scratch@localhost:55432/tracelab_scratch#?host=prod.example.com&database=hoist_prod", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch#frag", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?sslmode=disable#?host=x", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?host=a&host=b", False),
    ("postgresql+asyncpg://u:p@localhost/tracelab_scratch?dsn=postgresql://u:p@a.postgres.database.azure.com/x", False),
])
def test_scratch_url_guard(url, ok):
    assert migrate.scratch_url_ok(url) is ok


@pytest.mark.parametrize("url, host", [
    ("postgresql+asyncpg://u:p@Plenum.Postgres.Database.Azure.com:5432/x", "plenum.postgres.database.azure.com"),
    # every candidate the driver may use, the query's first, comma-joined (fail closed, never one guess)
    ("postgresql+asyncpg://u:p@localhost/x?host=Evil.Example.com", "evil.example.com,localhost"),
    ("postgresql+asyncpg://u:p@localhost/x?hostaddr=10.0.0.5", "10.0.0.5,localhost"),
    ("postgresql+asyncpg://u:p@localhost/x?sslmode=disable", "localhost"),
    ("postgresql+asyncpg://u:p@localhost/x#?host=a.postgres.database.azure.com", "a.postgres.database.azure.com,localhost"),
    ("postgresql+asyncpg://u:p@localhost/hoist_tracelab#?host=plenum.postgres.database.azure.com&database=hoist_tracelab",
     "plenum.postgres.database.azure.com,localhost"),
    # a repeated ?host= is a multi-host list and asyncpg tries the FIRST one first
    ("postgresql+asyncpg://u:p@localhost:5432/hoist_tracelab?host=A.postgres.database.azure.com:5432&host=localhost:5432",
     "a.postgres.database.azure.com:5432,localhost:5432,localhost"),
    # ?dsn= is handed to asyncpg, which connects to the dsn's host (read recursively)
    ("postgresql+asyncpg://u:p@/x?dsn=postgresql://u:p@A.postgres.database.azure.com/x", "a.postgres.database.azure.com"),
    ("postgresql+asyncpg://u:p@localhost/x?dsn=postgresql://u:p@a.postgres.database.azure.com/x",
     "a.postgres.database.azure.com,localhost"),
    ("postgresql+asyncpg://u:p@/x?dsn=postgresql://u:p@localhost/x?host=a.postgres.database.azure.com",
     "a.postgres.database.azure.com,localhost"),
    ("postgresql+asyncpg://u:p@/x?dsn=no-scheme@Db.Azure.Example", "db.azure.example"),
    ("", ""),
])
def test_url_host_is_the_host_the_driver_connects_to(monkeypatch, url, host):
    monkeypatch.delenv("PGHOST", raising=False)
    assert migrate.url_host(url) == host


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://u:p@localhost:5432/hoist_tracelab?host=plenum.postgres.database.azure.com:5432&host=localhost:5432",
    "postgresql+asyncpg://u:p@/hoist_tracelab?dsn=postgresql://u:p@plenum.postgres.database.azure.com/hoist_tracelab",
])
def test_boot_apply_sees_an_azure_host_behind_a_host_list_or_a_dsn(monkeypatch, url):
    monkeypatch.delenv("PGHOST", raising=False)
    monkeypatch.setenv("TRACE_LAB_APPLY_DATABASES", "hoist_tracelab")
    assert migrate.apply_refusal("hoist_tracelab", migrate.url_host(url)) == "azure_host"


def test_a_url_with_no_host_reads_pghost_as_the_driver_does(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_APPLY_DATABASES", "hoist_tracelab")
    monkeypatch.setenv("PGHOST", "Plenum.Postgres.Database.Azure.com")
    assert migrate.url_host("postgresql+asyncpg://u:p@/hoist_tracelab") == "plenum.postgres.database.azure.com"
    assert migrate.apply_refusal("hoist_tracelab", migrate.url_host("postgresql+asyncpg://u:p@/hoist_tracelab")) \
        == "azure_host"
    monkeypatch.delenv("PGHOST")
    assert migrate.url_host("postgresql+asyncpg://u:p@/hoist_tracelab") == ""


def test_schema_state_as_dict():
    st = migrate.SchemaState("behind", 2, 1, pending=[2], problems=["x"])
    d = st.as_dict()
    assert d["status"] == "behind" and d["pending"] == [2] and d["problems"] == ["x"]
    assert isinstance(st.checked_at, str) and st.checked_at.endswith("Z") and d["checked_at"] == st.checked_at


BASE = {
    "tables": ["jobs"],
    "columns": {"jobs": {"id": {"type": "uuid", "notnull": True, "default": "gen_random_uuid()", "identity": ""}}},
    "indexes": {"jobs_pkey": {"table": "jobs", "def": "CREATE UNIQUE INDEX jobs_pkey ON tracelab.jobs USING btree (id)"}},
    "constraints": {"jobs.jobs_pkey": {"type": "p", "def": "PRIMARY KEY (id)"}},
    "triggers": {},
    "functions": {"refuse_change()": "abc"},
}


def test_compare_catalog_names_each_difference():
    live = copy.deepcopy(BASE)
    live["columns"]["jobs"]["foo"] = {"type": "text", "notnull": False, "default": None, "identity": ""}
    del live["indexes"]["jobs_pkey"]
    live["constraints"]["jobs.jobs_pkey"]["def"] = "PRIMARY KEY (id, x)"
    live["functions"]["refuse_change()"] = "changed"
    problems, differs = migrate.compare_catalog(BASE, live, same_major=True)
    assert "jobs: extra column foo" in problems
    assert "jobs_pkey: missing" in problems
    assert "jobs.jobs_pkey: definition changed" in problems
    assert "refuse_change(): function body changed" in problems
    assert differs == []
    assert migrate.compare_catalog(BASE, copy.deepcopy(BASE)) == ([], [])


def test_definition_text_is_relaxed_across_major_versions():
    live = copy.deepcopy(BASE)
    live["constraints"]["jobs.jobs_pkey"]["def"] = "PRIMARY KEY ( id )x"
    problems, differs = migrate.compare_catalog(BASE, live, same_major=False)
    assert problems == [] and differs[0]["object"] == "jobs.jobs_pkey"
    live["columns"]["jobs"]["id"]["type"] = "text"
    problems, _ = migrate.compare_catalog(BASE, live, same_major=False)
    assert problems == ["jobs.id: type 'uuid' expected, 'text' found"]


def test_migrate_imports_no_application_code():
    text = Path(migrate.__file__).read_text(encoding="utf-8")
    assert "from src" not in text and "import src" not in text


# ── the shipped files ─────────────────────────────────────────────────────────────────────

def test_files_are_named_and_numbered_contiguously():
    found = migrate.files()
    assert found and found[0].name == "0001_foundation.sql"
    assert [f.version for f in found] == list(range(1, len(found) + 1))


def test_every_file_starts_with_lock_timeout():
    for f in migrate.files():
        first = next(line for line in f.sql.splitlines() if line.strip() and not line.strip().startswith("--"))
        assert first == "SET LOCAL lock_timeout = '3s';", f.name


def test_no_file_breaks_the_file_rules():
    for f in migrate.files():
        assert migrate.lint(f) == [], f.name


def test_shipped_files_are_never_edited():
    by_version = {f.version: f for f in migrate.files()}
    for version, pinned in migrate.SHIPPED_CHECKSUMS.items():
        assert version in by_version, f"{version:04d} was applied to a shared database and its file is gone"
        assert by_version[version].checksum == pinned, (
            f"{by_version[version].name} was applied to a shared database and has been edited: write a new file")


def test_foundation_creates_the_ledger_jobs_evidence_and_the_guard():
    sql = migrate.files()[0].sql
    for name in ("CREATE SCHEMA tracelab;", "CREATE TABLE tracelab.schema_migrations",
                 "CREATE TABLE tracelab.jobs", "CREATE TABLE tracelab.evidence_turns",
                 "CREATE TABLE tracelab.evidence_spans", "CREATE FUNCTION tracelab.refuse_change()",
                 "ux_jobs_one_open_per_scope", "evidence_turns_rows", "evidence_spans_truncate"):
        assert name in sql, name
    assert "'replay'" not in sql and "turn_meta" not in sql and "plenum" not in sql


# ── verify and apply without a database ───────────────────────────────────────────────────

import asyncio  # noqa: E402


class _Dead:
    def connect(self):
        raise OSError("connection refused")

    def begin(self):
        raise OSError("connection refused")


async def test_verify_reports_unreadable_when_the_database_cannot_be_reached():
    st = await migrate.verify(_Dead())
    assert st.status == "unreadable" and "connection refused" in st.problems[0]
    assert st.expected_version >= 1 and st.applied_version is None


async def test_verify_reports_unreadable_when_the_files_cannot_load(tmp_path):
    _write(tmp_path, "0002_x.sql")
    st = await migrate.verify(_Dead(), directory=tmp_path)
    assert st.status == "unreadable" and st.problems[0].startswith("files:")


async def test_apply_pending_runs_nothing_unless_absent_or_behind(monkeypatch):
    async def drifted(engine, *, directory=None):
        return migrate.SchemaState("drifted", 1, 1, problems=["jobs: extra column foo"])

    async def never(*a, **k):
        raise AssertionError("applied on a drifted database")

    monkeypatch.setattr(migrate, "verify", drifted)
    monkeypatch.setattr(migrate, "_apply_one", never)
    st = await migrate.apply_pending(_Dead(), applied_by="pytest")
    assert st.status == "drifted"


async def test_apply_pending_applies_only_through_the_named_version(monkeypatch, tmp_path):
    _write(tmp_path, "0001_a.sql")
    _write(tmp_path, "0002_b.sql")
    states = iter([migrate.SchemaState("behind", 2, None, pending=[1, 2]),
                   migrate.SchemaState("behind", 2, 1, pending=[2])])

    async def fake_verify(engine, *, directory=None):
        return next(states)

    applied = []

    async def fake_apply_one(engine, f, applied_by):
        applied.append((f.version, applied_by))
        return None

    monkeypatch.setattr(migrate, "verify", fake_verify)
    monkeypatch.setattr(migrate, "_apply_one", fake_apply_one)
    st = await migrate.apply_pending(_Dead(), applied_by="cli:hussain", through=1, directory=tmp_path)
    assert applied == [(1, "cli:hussain")]
    assert st.status == "behind" and st.applied_version == 1


async def test_a_failed_file_makes_the_state_unreadable(monkeypatch, tmp_path):
    _write(tmp_path, "0001_a.sql")
    states = iter([migrate.SchemaState("absent", 1, None, pending=[1]),
                   migrate.SchemaState("absent", 1, None, pending=[1])])

    async def fake_verify(engine, *, directory=None):
        return next(states)

    async def failing(engine, f, applied_by):
        return "0001: failed at: SELECT 1 / 0: division by zero"

    monkeypatch.setattr(migrate, "verify", fake_verify)
    monkeypatch.setattr(migrate, "_apply_one", failing)
    st = await migrate.apply_pending(_Dead(), applied_by="pytest", directory=tmp_path)
    assert st.status == "unreadable" and st.problems[0].startswith("0001: failed at")


class _LockedBegin:
    async def __aenter__(self):
        raise RuntimeError("canceling statement due to lock timeout")

    async def __aexit__(self, *a):
        return False


class _Locked:
    def begin(self):
        return _LockedBegin()


async def test_a_lock_timeout_is_retried_then_reported(monkeypatch, tmp_path):
    _write(tmp_path, "0001_a.sql")
    monkeypatch.setattr(migrate, "LOCK_RETRY_SLEEP_S", 0)
    problem = await migrate._apply_one(_Locked(), migrate.files(tmp_path)[0], "pytest")
    assert problem.startswith("0001: could not get its lock after 4 attempts")


def test_applied_by_is_bounded():
    with pytest.raises(ValueError):
        asyncio.run(migrate.apply_pending(_Dead(), applied_by=""))


def test_error_state_is_unreadable():
    st = migrate.error_state("check failed: boom")
    assert st.status == "unreadable" and st.problems == ["check failed: boom"]
    assert st.expected_version == len(migrate.files())


def test_db_host_reads_the_url(monkeypatch):
    assert migrate.db_host("postgresql+asyncpg://u:p@Plenum.Postgres.Database.Azure.com:5432/x") == \
        "plenum.postgres.database.azure.com"
    monkeypatch.setenv("TRACE_LAB_DB_URL", "postgresql+asyncpg://u:p@127.0.0.1:55432/tracelab_scratch")
    assert migrate.db_host() == "127.0.0.1"
    # a ?host= parameter overrides the authority part, and apply_refusal must see the real host
    # (db_host returns every candidate host, comma-joined, so the guard fails closed)
    assert migrate.db_host("postgresql+asyncpg://u:p@localhost/x?host=Plenum.Postgres.Database.Azure.com") == \
        "plenum.postgres.database.azure.com,localhost"
    # ... and so does one hidden after a '#', which urlparse never sees
    assert migrate.db_host("postgresql+asyncpg://u:p@localhost/x#?host=Plenum.Postgres.Database.Azure.com") == \
        "plenum.postgres.database.azure.com,localhost"
    assert migrate.apply_refusal(
        "tracelab_scratch",
        migrate.db_host("postgresql+asyncpg://u:p@localhost/tracelab_scratch?host=x.postgres.database.azure.com"),
    ) == "azure_host"
    # a repeated ?host= (asyncpg tries the FIRST), a ?dsn=, or no host at all (the driver reads PGHOST)
    monkeypatch.setenv("TRACE_LAB_APPLY_DATABASES", "hoist_tracelab")
    monkeypatch.delenv("PGHOST", raising=False)
    for url in ("postgresql+asyncpg://u:p@localhost:5432/hoist_tracelab"
                "?host=a.postgres.database.azure.com:5432&host=localhost:5432",
                "postgresql+asyncpg://u:p@/hoist_tracelab?dsn=postgresql://u:p@a.postgres.database.azure.com/hoist_tracelab"):
        assert migrate.apply_refusal("hoist_tracelab", migrate.db_host(url)) == "azure_host"
    monkeypatch.setenv("PGHOST", "a.postgres.database.azure.com")
    assert migrate.apply_refusal("hoist_tracelab", migrate.db_host("postgresql+asyncpg://u:p@/hoist_tracelab")) == \
        "azure_host"


# ── the CLI ───────────────────────────────────────────────────────────────────────────────

import io  # noqa: E402
import json  # noqa: E402


class _TTY(io.StringIO):
    def isatty(self):
        return True


class _Eng:
    async def dispose(self):
        return None


def _cli_fakes(monkeypatch, status="behind", database="hoist_tracelab"):
    async def dbname(engine):
        return database

    async def state(engine, *, directory=None):
        return migrate.SchemaState(status, 1, None, pending=[1] if status in ("absent", "behind") else [])

    async def no_jobs(engine):
        return []

    monkeypatch.setattr(migrate, "_engine", lambda: _Eng())
    monkeypatch.setattr(migrate, "current_database", dbname)
    monkeypatch.setattr(migrate, "verify", state)
    monkeypatch.setattr(migrate, "_open_jobs", no_jobs)
    monkeypatch.delenv("TRACE_LAB_DB_URL", raising=False)


def _never(what):
    async def never(*a, **k):
        raise AssertionError(what)
    return never


APPLY = ["apply", "--expect-db", "hoist_tracelab", "--through", "0001", "--applied-by", "hussain"]


def test_cli_apply_refuses_without_a_terminal(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))

    def no_engine():
        raise AssertionError("no database may be touched")

    monkeypatch.setattr(migrate, "_engine", no_engine)
    assert migrate.main(APPLY) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_apply_needs_through():
    with pytest.raises(SystemExit):
        migrate.main(["apply", "--expect-db", "hoist_tracelab", "--applied-by", "hussain"])


def test_cli_snapshot_refuses_a_shared_database(capsys):
    assert migrate.main(["snapshot", "--db-url",
                         "postgresql+asyncpg://u:p@plenum.postgres.database.azure.com/hoist_tracelab"]) == 2
    assert "scratch" in capsys.readouterr().err


def test_cli_apply_refuses_another_database(monkeypatch, capsys):
    _cli_fakes(monkeypatch, database="hoistra_test")
    monkeypatch.setattr("sys.stdin", _TTY(""))
    monkeypatch.setattr(migrate, "apply_pending", _never("must not apply"))
    assert migrate.main(APPLY) == 2
    assert "not 'hoist_tracelab'" in capsys.readouterr().err


def test_cli_apply_refuses_unless_absent_or_behind(monkeypatch, capsys):
    _cli_fakes(monkeypatch, status="drifted")
    monkeypatch.setattr("sys.stdin", _TTY("hoist_tracelab\n"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied on a drifted database"))
    assert migrate.main(APPLY) == 2
    assert "status is drifted" in capsys.readouterr().err


def test_cli_apply_needs_the_typed_database_name(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("nope\n"))
    monkeypatch.setattr(migrate, "apply_pending", _never("applied without the typed confirmation"))
    assert migrate.main(APPLY) == 1
    out = capsys.readouterr()
    assert "nothing ran" in out.err
    assert "SET LOCAL lock_timeout = '3s';" in out.out and "pending 0001_foundation.sql" in out.out


def test_cli_apply_prints_the_shipped_checksum_line(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("hoist_tracelab\n"))
    seen = {}

    async def apply(engine, *, applied_by, through=None, dry_run=False, directory=None):
        seen.update(by=applied_by, through=through, dry=dry_run)
        return migrate.SchemaState("ready", 1, 1)

    monkeypatch.setattr(migrate, "apply_pending", apply)
    assert migrate.main(APPLY) == 0
    out = capsys.readouterr().out
    f = migrate.files()[0]
    assert seen == {"by": "cli:hussain", "through": 1, "dry": False}
    assert f'    1: "{f.checksum}",  # 0001_foundation.sql' in out


def test_cli_dry_run_that_failed_exits_1(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("hoist_tracelab\n"))

    async def apply(engine, *, applied_by, through=None, dry_run=False, directory=None):
        return migrate.SchemaState("behind", 1, None, pending=[1],
                                   problems=["0001: dry run failed at: CREATE TABLE x: boom"])

    monkeypatch.setattr(migrate, "apply_pending", apply)
    assert migrate.main([*APPLY, "--dry-run"]) == 1
    assert "dry run failed" in capsys.readouterr().out


def test_cli_dry_run_that_passed_exits_0(monkeypatch, capsys):
    _cli_fakes(monkeypatch)
    monkeypatch.setattr("sys.stdin", _TTY("hoist_tracelab\n"))

    async def apply(engine, *, applied_by, through=None, dry_run=False, directory=None):
        return migrate.SchemaState("behind", 1, None, pending=[1])

    monkeypatch.setattr(migrate, "apply_pending", apply)
    assert migrate.main([*APPLY, "--dry-run"]) == 0


def test_cli_status_prints_the_state(monkeypatch, capsys):
    _cli_fakes(monkeypatch, status="ready")
    assert migrate.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_cli_status_exits_1_when_not_ready(monkeypatch, capsys):
    _cli_fakes(monkeypatch, status="absent")
    assert migrate.main(["status"]) == 1
    assert json.loads(capsys.readouterr().out)["pending"] == [1]


def test_cli_engine_is_the_lab_engine(monkeypatch):
    from trace_lab import db

    sentinel = object()
    monkeypatch.setattr(db, "lab_engine", lambda: sentinel)
    assert migrate._engine() is sentinel
