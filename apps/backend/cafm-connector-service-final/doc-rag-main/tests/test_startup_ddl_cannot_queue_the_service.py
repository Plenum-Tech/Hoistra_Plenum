"""Startup DDL takes no lock it does not need, and never waits long for one it does.

Measured 17 Sep 2026: this service's `ALTER TABLE ingestion_documents ADD COLUMN IF NOT EXISTS`
waited 26 minutes for an exclusive lock behind two idle-in-transaction sessions, and every reader
of the table queued behind the waiting ALTER — 47 sessions by the time anyone looked. All of
those columns already existed. The module is imported from its file so no app import is needed.
"""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "docrag_ddl", Path(__file__).resolve().parents[1] / "app" / "db" / "ddl.py")
ddl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ddl)


# ── fakes: an engine that records what was executed ───────────────────────────────────

class _Result:
    def __init__(self, value): self._v = value
    def scalar(self): return self._v


class _Conn:
    def __init__(self, engine): self.e = engine
    def execute(self, clause, params=None):
        sql = str(clause)
        self.e.executed.append(sql)
        if "information_schema.columns" in sql:
            return _Result(1 if self.e.column_present else None)
        if sql.startswith("SET LOCAL"):
            return _Result(None)
        self.e.alters += 1
        exc = self.e.errors.pop(0) if self.e.errors else None
        if exc:
            raise exc
        return _Result(None)
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _Engine:
    def __init__(self, *, column_present=False, errors=()):
        self.column_present = column_present
        self.errors = list(errors)
        self.executed: list[str] = []
        self.alters = 0
    def connect(self): return _Conn(self)
    def begin(self): return _Conn(self)


class _Log:
    def __init__(self): self.lines: list[str] = []
    def info(self, m): self.lines.append("I " + m)
    def warning(self, m): self.lines.append("W " + m)


ALTER = "ALTER TABLE plenum_cafm.ingestion_documents ADD COLUMN IF NOT EXISTS mime_type VARCHAR(128)"
LOCK = Exception("canceling statement due to lock timeout")


# ── the pre-check ──────────────────────────────────────────────────────────────────────

class TestParsing:

    def test_a_qualified_add_column_is_parsed(self):
        assert ddl.parse_add_column(ALTER) == ("plenum_cafm", "ingestion_documents", "mime_type")

    def test_an_unqualified_table_has_no_schema(self):
        assert ddl.parse_add_column("ALTER TABLE row_semantic_index ADD COLUMN IF NOT EXISTS pk_column VARCHAR(128)") \
            == (None, "row_semantic_index", "pk_column")

    def test_add_without_the_word_column_and_mixed_case_still_parse(self):
        assert ddl.parse_add_column("alter table t add if not exists c int") == (None, "t", "c")

    def test_everything_else_is_not_pre_checkable(self):
        for stmt in ("ALTER TABLE plenum_cafm.ingestion_documents ALTER COLUMN source_type SET DEFAULT 'document'",
                     "ALTER TABLE t ADD COLUMN c INT",  # no IF NOT EXISTS: it must run and may fail
                     "DO $$ BEGIN ALTER TABLE t ADD COLUMN IF NOT EXISTS c INT; END $$",
                     "CREATE INDEX IF NOT EXISTS i ON t (c)", ""):
            assert ddl.parse_add_column(stmt) is None, stmt

    def test_only_a_lock_timeout_is_a_lock_timeout(self):
        assert ddl.is_lock_timeout(LOCK)
        assert ddl.is_lock_timeout(Exception("LockNotAvailableError: ..."))
        assert not ddl.is_lock_timeout(Exception('relation "x" does not exist'))


class TestRunningOneStatement:

    def test_an_existing_column_takes_no_lock(self):
        e = _Engine(column_present=True)
        assert ddl.run_ddl(e, ALTER, log=_Log()) == ddl.ALREADY_APPLIED
        assert e.alters == 0
        assert all("information_schema" in s for s in e.executed)

    def test_a_missing_column_is_added_under_a_lock_timeout(self):
        e = _Engine(column_present=False)
        assert ddl.run_ddl(e, ALTER, log=_Log()) == ddl.APPLIED
        assert e.alters == 1
        assert any(s.startswith("SET LOCAL lock_timeout = '5s'") for s in e.executed)

    def test_a_statement_that_cannot_be_pre_checked_runs_without_asking(self):
        e = _Engine()
        stmt = "ALTER TABLE plenum_cafm.document_chunks ALTER COLUMN source_filename DROP NOT NULL"
        assert ddl.run_ddl(e, stmt, log=_Log()) == ddl.APPLIED
        assert not any("information_schema" in s for s in e.executed)

    def test_a_lock_timeout_backs_off_and_retries_then_succeeds(self):
        e = _Engine(errors=[LOCK, LOCK])
        slept = []
        assert ddl.run_ddl(e, ALTER, log=_Log(), sleep=slept.append) == ddl.APPLIED
        assert e.alters == 3 and slept == [ddl.LOCK_RETRY_SLEEP_S] * 2

    def test_a_lock_that_never_comes_is_skipped_not_waited_for(self):
        e = _Engine(errors=[LOCK] * ddl.LOCK_RETRIES)
        log = _Log()
        assert ddl.run_ddl(e, ALTER, log=log, sleep=lambda s: None) == ddl.LOCK_SKIPPED
        assert e.alters == ddl.LOCK_RETRIES
        assert any("skipped after" in l for l in log.lines)

    def test_any_other_error_is_skipped_without_retrying(self):
        e = _Engine(errors=[Exception('relation "nope" does not exist')])
        assert ddl.run_ddl(e, "ALTER TABLE nope ADD COLUMN IF NOT EXISTS c INT", log=_Log(), sleep=lambda s: None) == ddl.FAILED
        assert e.alters == 1

    def test_a_failing_pre_check_falls_through_to_the_statement(self):
        class _Broken(_Engine):
            def connect(self): raise RuntimeError("no catalogue access")
        e = _Broken()
        assert ddl.run_ddl(e, ALTER, log=_Log()) == ddl.APPLIED
        assert e.alters == 1


class TestTheSessionUsesIt:

    def test_run_migrations_runs_each_statement_through_run_ddl(self):
        src = (Path(__file__).resolve().parents[1] / "app" / "db" / "session.py").read_text(encoding="utf-8")
        body = src[src.index("def _run_migrations"):src.index("def get_db")]
        assert "run_ddl(engine, stmt" in body
        assert "with engine.begin() as conn:\n            for stmt in stmts" not in body, "one transaction for all statements is what queued the service"
