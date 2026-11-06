"""The migration ledger's rules that need no database.

What a file EXECUTES decides whether it runs again, not how it is written: these files carry
paragraphs of prose, and if a comment edit counted as a change, every edit to the prose would
re-run the file -- locks and all -- on the next start.
"""
from __future__ import annotations

from src import db


def _checksum(sql: str) -> str:
    return db._statements_checksum(db._split_sql(sql))


BASE = """
-- why this file exists
ALTER TABLE plenum_cafm.users ADD COLUMN IF NOT EXISTS nickname TEXT;
CREATE INDEX IF NOT EXISTS ix_users_nickname ON plenum_cafm.users (nickname);
"""


def test_a_comment_is_not_a_change():
    edited = BASE.replace("-- why this file exists",
                          "-- why this file exists, explained at much greater length\n-- and more")
    assert _checksum(edited) == _checksum(BASE)


def test_reindenting_is_not_a_change():
    edited = BASE.replace("CREATE INDEX IF NOT EXISTS ix_users_nickname ON",
                          "CREATE INDEX IF NOT EXISTS ix_users_nickname\n        ON")
    assert _checksum(edited) == _checksum(BASE)


def test_a_changed_statement_is_a_change():
    assert _checksum(BASE.replace("nickname TEXT", "nickname VARCHAR(40)")) != _checksum(BASE)


def test_an_added_statement_is_a_change():
    assert _checksum(BASE + "\nDROP INDEX IF EXISTS plenum_cafm.ix_old;\n") != _checksum(BASE)


def test_the_marker_is_read_only_on_a_line_of_its_own():
    assert db._EVERY_START.search("-- intro\n--\n-- ledger: every-start\n--\nSELECT 1;")
    assert not db._EVERY_START.search("-- this file is NOT ledger: every-start, it says so\n")
    assert not db._EVERY_START.search("SELECT 1;\n")


def test_exactly_the_files_built_from_the_catalogue_run_every_start():
    """udr_scoped_views.sql makes one view per table FROM information_schema, so a table added
    since the last start needs it to run again; phase3_stamp_single_org.sql fills NULLs written
    since the last start. Every other file's result is fixed by its own text, so the ledger may
    skip it. A new every-start file has to be lock-free when idle -- that is a decision, so a new
    marker should fail here until somebody makes it on purpose."""
    marked = sorted(f.name for f in db._MIGRATIONS_DIR.glob("*.sql")
                    if db._EVERY_START.search(f.read_text(encoding="utf-8")))
    assert marked == ["phase3_stamp_single_org.sql", "udr_scoped_views.sql"]


def test_the_marker_is_a_comment_and_changes_nothing_that_runs():
    src = (db._MIGRATIONS_DIR / "udr_scoped_views.sql").read_text(encoding="utf-8")
    assert all("ledger: every-start" not in s for s in db._split_sql(src))


def test_the_runner_still_runs_each_statement_through_the_lock_timeout():
    """The ledger decides WHETHER a file runs; when it does, each statement must still go
    through _run_ddl_statement, which carries the lock timeout."""
    import inspect
    assert "_run_ddl_statement(stmt)" in inspect.getsource(db.apply_sql_migrations)
