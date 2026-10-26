"""0005_judges.sql read as text (no database): it follows the migration file rules, creates exactly the five judge
tables in schema tracelab, names a company only where a turn is stored (judge_split_members, judge_predictions),
keeps no foreign key to any chat table, and brings its own five guard functions. Nothing here touches a database."""
from __future__ import annotations

import re

from trace_lab import jobs, migrate

TABLES = ["judge_splits", "judge_split_members", "judges", "judge_predictions", "judge_runs"]
GUARDS = ["judge_splits_guard()", "judges_guard()", "judges_examples_from_train()", "judge_predictions_guard()",
          "judge_runs_guard()"]


def _judges():
    found = {f.version: f for f in migrate.files()}
    assert 5 in found and found[5].name == "0005_judges.sql", sorted(found)
    return found[5]


def _sql() -> str:
    return _judges().sql


def _table(table: str) -> str:
    m = re.search(rf"CREATE TABLE tracelab\.{table} \((.*?)\n\);", _sql(), re.S)
    assert m, table
    return m.group(1)


def _check_list(body: str, column: str) -> list[str]:
    m = re.search(rf"CHECK \({column} IN \(([^)]*)\)", body)
    assert m, column
    return [v.strip().strip("'") for v in re.sub(r"\s+", " ", m.group(1)).split(",")]


def test_the_file_is_fifth_follows_the_file_rules_and_starts_with_the_lock_timeout():
    f = _judges()
    assert migrate.lint(f) == []
    assert migrate.split_statements(f.sql)[0] == migrate.FIRST_STATEMENT
    first = [ln for ln in f.sql.splitlines() if ln.strip() and not ln.strip().startswith("--")][0]
    assert first == "SET LOCAL lock_timeout = '3s';"


def test_it_creates_the_five_judge_tables_and_nothing_outside_tracelab():
    sql = _sql()
    stmts = migrate.split_statements(sql)
    assert [m.group(1) for s in stmts if (m := re.match(r"^CREATE TABLE (\S+)", s))] == [f"tracelab.{t}" for t in TABLES]
    assert [m.group(1) for s in stmts if (m := re.match(r"^CREATE FUNCTION tracelab\.(\S+)", s))] == GUARDS
    for old in ("plenum_cafm", "plenum_tracelab", "turn_meta"):
        assert old not in sql, old
    assert "IF NOT EXISTS" not in sql.upper()
    assert not re.search(r"\b(GRANT|REVOKE|CONCURRENTLY)\b", sql, re.I)
    assert all(s.strip().split()[0].upper() in ("SET", "CREATE", "COMMENT") for s in stmts)


def test_nothing_in_it_crosses_into_the_application_database():
    """A turn is named by id only: no foreign key to a chat table, and none from a turn column to anything."""
    sql = _sql()
    refs = re.findall(r"REFERENCES\s+(\S+)", sql)
    assert refs and all(r.startswith("tracelab.") for r in refs)
    for table in ("judge_split_members", "judge_predictions"):
        assert re.search(r"^\s+turn_id\s+text NOT NULL CHECK \(length\(turn_id\) BETWEEN 1 AND 200\),$",
                         _table(table), re.M), table
    assert not re.search(r"turn_id\s+\w+[^,\n]*REFERENCES", sql)


def test_only_the_tables_that_store_a_turn_name_a_company():
    for t in ("judge_split_members", "judge_predictions"):
        assert re.search(r"^\s+organization_id\s+uuid NOT NULL,$", _table(t), re.M), t
    for t in ("judge_splits", "judges", "judge_runs"):
        assert "organization_id" not in _table(t), t


def test_keys_and_the_one_held_out_test_per_judge():
    assert "PRIMARY KEY (split_id, turn_id)" in _table("judge_split_members")
    assert "PRIMARY KEY (prompt_hash, turn_id)" in _table("judge_predictions")
    sql = _sql()
    assert "CREATE UNIQUE INDEX ux_judge_runs_one_test ON tracelab.judge_runs (judge_id) WHERE split = 'test';" in sql
    assert "CREATE UNIQUE INDEX ux_judge_splits_active ON tracelab.judge_splits (mode_id) WHERE status = 'active';" in sql
    assert "UNIQUE (mode_id, version)" in _table("judge_splits") and "UNIQUE (mode_id, version)" in _table("judges")


def test_every_turn_column_has_an_index_that_leads_with_it():
    sql = _sql()
    assert "CREATE INDEX ix_judge_split_members_turn ON tracelab.judge_split_members (turn_id);" in sql
    assert "CREATE INDEX ix_judge_predictions_turn ON tracelab.judge_predictions (turn_id);" in sql


def test_the_value_lists_the_later_code_relies_on():
    assert _check_list(_table("judges"), "kind") == ["llm", "code"]
    assert _check_list(_table("judges"), "status") == ["draft", "frozen"]
    assert _check_list(_table("judge_splits"), "status") == ["active", "retired"]
    assert _check_list(_table("judge_split_members"), "split") == ["train", "dev", "test"]
    assert _check_list(_table("judge_runs"), "split") == ["dev", "test"]
    assert _check_list(_table("judge_runs"), "status") == ["queued", "running", "done", "incomplete", "capped",
                                                           "failed", "interrupted"]
    assert _check_list(_table("judge_predictions"), "status") == ["decided", "error", "undecidable"]
    assert _check_list(_table("judges"), "decision") == ["use", "do_not_use"]


def test_a_split_member_cites_a_label_source_the_labels_table_accepts():
    """The member's label_source is the source of the label it was taken from (0004 labels.source)."""
    labels = {f.version: f for f in migrate.files()}[4].sql
    body = re.search(r"CREATE TABLE tracelab\.labels \((.*?)\n\);", labels, re.S).group(1)
    assert set(_check_list(_table("judge_split_members"), "label_source")) == set(_check_list(body, "source"))


def test_judge_runs_belong_to_jobs_that_spend():
    """Eval spend is read from tracelab.jobs.usd_spent, so a judge run needs a job and the job kind exists."""
    assert re.search(r"^\s+job_id\s+uuid REFERENCES tracelab\.jobs\(id\),$", _table("judge_runs"), re.M)
    assert "judge_run" in jobs.KINDS


def test_history_tables_use_the_guards_and_every_guard_has_its_truncate_twin():
    sql = _sql()
    assert "EXECUTE FUNCTION tracelab.refuse_change()" in sql                  # the 0001 guard, reused for members
    assert "CREATE TRIGGER judge_split_members_append_only BEFORE UPDATE OR DELETE ON tracelab.judge_split_members" in sql
    for table, function in (("judge_splits", "judge_splits_guard"), ("judges", "judges_guard"),
                            ("judge_predictions", "judge_predictions_guard")):
        assert f"CREATE TRIGGER {function} BEFORE UPDATE OR DELETE ON tracelab.{table}" in sql
        assert f"EXECUTE FUNCTION tracelab.{function}()" in sql
    assert "CREATE TRIGGER judge_runs_guard BEFORE INSERT OR UPDATE OR DELETE ON tracelab.judge_runs" in sql
    assert "CREATE TRIGGER judges_examples_from_train BEFORE INSERT ON tracelab.judges" in sql
    for table in TABLES:
        assert re.search(rf"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON tracelab\.{table}\n"
                         rf"\s+FOR EACH STATEMENT", sql), table


def test_the_purge_hatch_is_the_foundation_one_and_never_frees_a_judge():
    """The setting is tracelab.allow_purge (0001), set by a company offboarding. A judge version is never deleted,
    hatch or not."""
    sql = _sql()
    assert "current_setting('tracelab.allow_purge', true)" in sql
    assert re.findall(r"current_setting\('([^']*)'", sql) and all(
        name == "tracelab.allow_purge" for name in re.findall(r"current_setting\('([^']*)'", sql))
    body = re.search(r"CREATE FUNCTION tracelab\.judges_guard\(\).*?\n\$\$;", sql, re.S).group(0)
    assert "a judge version is never deleted" in body
    # DELETE and TRUNCATE are refused outright: the hatch is only consulted for the prompt scrub that follows
    assert body.index("never deleted") < body.index("NOT hatch") < body.index("the prompt of")
