"""0006_watch.sql read as text (no database): it follows the migration file rules, creates exactly the four Watch tables
and the one guard function in schema tracelab, alters no earlier table, names a company where a turn is stored (and
leaves it open only on the platform wide monitoring batch), keeps no foreign key to any chat table, and agrees with the
earlier files on the golden run purposes, the 20 run cap and the job kind. Nothing here touches a database."""
from __future__ import annotations

import re

from trace_lab import jobs, migrate, runner

TABLES = ["monitor_batches", "monitor_verdicts", "eval_cases", "eval_case_runs"]


def _watch():
    found = {f.version: f for f in migrate.files()}
    assert 6 in found and found[6].name == "0006_watch.sql", sorted(found)
    return found[6]


def _sql() -> str:
    return _watch().sql


def _earlier(version: int) -> str:
    return {f.version: f for f in migrate.files()}[version].sql


def _table(table: str, sql: str | None = None) -> str:
    m = re.search(rf"CREATE TABLE tracelab\.{table} \((.*?)\n\);", sql or _sql(), re.S)
    assert m, table
    return m.group(1)


def _check_list(body: str, column: str) -> list[str]:
    m = re.search(rf"CHECK \({column} IN \(([^)]*)\)", body)
    assert m, column
    return [v.strip().strip("'") for v in re.sub(r"\s+", " ", m.group(1)).split(",")]


def test_the_file_is_sixth_follows_the_file_rules_and_starts_with_the_lock_timeout():
    f = _watch()
    assert migrate.lint(f) == []
    assert migrate.split_statements(f.sql)[0] == migrate.FIRST_STATEMENT
    first = [ln for ln in f.sql.splitlines() if ln.strip() and not ln.strip().startswith("--")][0]
    assert first == "SET LOCAL lock_timeout = '3s';"


def test_it_creates_the_four_watch_tables_and_one_guard_and_changes_nothing_earlier():
    sql = _sql()
    stmts = migrate.split_statements(sql)
    assert [m.group(1) for s in stmts if (m := re.match(r"^CREATE TABLE (\S+)", s))] == [f"tracelab.{t}" for t in TABLES]
    assert [m.group(1) for s in stmts if (m := re.match(r"^CREATE FUNCTION (\S+)", s))] == [
        "tracelab.monitor_verdicts_guard()"]
    # only SET, CREATE and COMMENT: no ALTER, DROP, INSERT, UPDATE or DELETE touches a table another file made
    assert all(s.split()[0].upper() in ("SET", "CREATE", "COMMENT") for s in stmts)
    assert not re.search(r"^(ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE)\b", sql, re.M | re.I)
    for old in ("plenum_cafm", "plenum_tracelab", "turn_meta"):
        assert old not in sql, old
    assert "IF NOT EXISTS" not in sql.upper()
    assert not re.search(r"\b(GRANT|REVOKE|CONCURRENTLY)\b", sql, re.I)


def test_nothing_in_it_crosses_into_the_application_database():
    """A turn is named by id only: no foreign key to a chat table, and none from a turn column to anything."""
    sql = _sql()
    refs = re.findall(r"REFERENCES\s+(\S+)", sql)
    assert refs and all(r.startswith("tracelab.") for r in refs)
    assert sorted(set(refs)) == sorted(f"tracelab.{t}" for t in (
        "eval_cases", "golden_cases", "golden_runs", "jobs", "judges", "modes", "monitor_batches"))
    assert not re.search(r"turn_id\s+\w+[^,\n]*REFERENCES", sql)
    assert re.search(r"^\s+turn_id\s+text NOT NULL CHECK \(length\(turn_id\) BETWEEN 1 AND 200\),$",
                     _table("monitor_verdicts"), re.M)
    assert re.search(r"^\s+source_turn_id\s+text CHECK \(source_turn_id IS NULL OR length\(source_turn_id\) BETWEEN 1 AND 200\),$",
                     _table("eval_cases"), re.M)


def test_every_turn_column_has_an_index_that_leads_with_it():
    sql = _sql()
    assert "CREATE INDEX ix_monitor_verdicts_turn ON tracelab.monitor_verdicts (turn_id);" in sql
    assert "CREATE INDEX ix_eval_cases_source_turn ON tracelab.eval_cases (source_turn_id);" in sql


def test_a_company_is_named_on_every_table_and_left_open_only_for_the_platform_batch():
    assert re.search(r"^\s+organization_id\s+uuid,$", _table("monitor_batches"), re.M)
    for t in ("monitor_verdicts", "eval_cases"):
        assert re.search(r"^\s+organization_id\s+uuid NOT NULL,$", _table(t), re.M), t
    assert "organization_id" not in _table("eval_case_runs")          # its company comes through the case
    assert "COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)) WHERE status IN ('queued','running')" in _sql()


def test_keys_and_uniqueness_the_later_code_relies_on():
    sql = _sql()
    body = _table("monitor_verdicts")
    assert "CONSTRAINT ux_monitor_verdicts_evaluator_turn UNIQUE (evaluator_key, turn_id)" in body   # ON CONFLICT target
    assert "CREATE UNIQUE INDEX ux_monitor_batches_active ON tracelab.monitor_batches" in sql
    assert "CREATE UNIQUE INDEX ux_eval_cases_current ON tracelab.eval_cases (organization_id, case_key)\n    WHERE status <> 'superseded';" in sql
    assert "CONSTRAINT ux_eval_cases_key_version UNIQUE (organization_id, case_key, version)" in _table("eval_cases")
    assert "CONSTRAINT ux_eval_case_runs_case_run UNIQUE (eval_case_id, golden_run_id)" in _table("eval_case_runs")


def test_the_value_lists_the_later_code_relies_on():
    assert _check_list(_table("monitor_batches"), "status") == ["queued", "running", "done", "capped", "failed",
                                                                "interrupted"]
    assert _check_list(_table("monitor_verdicts"), "status") == ["decided", "error", "undecidable", "not_applicable"]
    assert _check_list(_table("eval_cases"), "status") == ["proposed", "draft", "baselining", "active", "retired",
                                                           "superseded"]
    assert _check_list(_table("eval_cases"), "kind") == ["unclassified", "regression", "capability"]
    assert _check_list(_table("eval_cases"), "source_type") == ["live_turn", "monitor", "golden_case"]
    assert _check_list(_table("eval_case_runs"), "purpose") == ["baseline", "release_check", "repeat"]
    assert _check_list(_table("eval_case_runs"), "decision") == ["pass", "block", "incomplete"]


def test_a_capability_baseline_is_a_rate_a_five_run_baseline_can_give_short_of_all_five():
    m = re.search(r"baseline_pass_rate\s+numeric\(3,2\) CHECK \(baseline_pass_rate IN \(([^)]*)\)\)", _sql())
    assert m
    rates = [float(v) for v in m.group(1).split(",")]
    assert rates == [c / 5 for c in range(5)]        # 5 of 5 is a regression case, so 1.0 is not a capability rate


def test_eval_case_runs_agree_with_the_golden_runs_they_cite():
    """A case run names a golden run, so its purpose is one of the run purposes, its k obeys the run cap, and the
    runs that spend are jobs of a kind that exists."""
    golden = _table("golden_runs", _earlier(2))
    runs_purposes = _check_list(golden, "purpose")
    assert set(_check_list(_table("eval_case_runs"), "purpose")) <= set(runs_purposes)
    cap = int(re.search(r"k\s+integer .*CHECK \(k BETWEEN 1 AND (\d+)\)", golden).group(1))
    assert f"k               integer NOT NULL CHECK (k BETWEEN 1 AND {cap})" in _table("eval_case_runs")
    assert cap == runner.HARD_MAX_K
    assert "monitor_batch" in jobs.KINDS and "golden_run" in jobs.KINDS
    assert re.search(r"^\s+job_id\s+uuid REFERENCES tracelab\.jobs \(id\),$", _table("monitor_batches"), re.M)


def test_the_regression_cases_set_and_the_monitor_strategy_belong_to_earlier_files():
    """0006 relies on them and never recreates them: the Regression cases set is 0002 (kind regression, version 0), the
    monitor_flagged sampling strategy is 0003."""
    sets = _earlier(2)
    assert "CREATE UNIQUE INDEX ux_golden_sets_one_regression ON tracelab.golden_sets (organization_id) WHERE kind = 'regression';" in sets
    assert "(kind = 'regression') = (version = 0)" in sets
    assert "'monitor_flagged'" in _earlier(3)
    sql = _sql()
    assert "golden_sets" not in sql and "sample_batches" not in sql      # neither table is created or altered here


def test_the_guard_keeps_a_decided_verdict_and_has_its_truncate_twin():
    sql = _sql()
    body = re.search(r"CREATE FUNCTION tracelab\.monitor_verdicts_guard\(\).*?\n\$\$;", sql, re.S).group(0)
    assert "OLD.status = 'decided'" in body and "a decided verdict is never changed" in body
    assert "CREATE TRIGGER monitor_verdicts_guard BEFORE UPDATE OR DELETE ON tracelab.monitor_verdicts" in sql
    assert re.search(r"CREATE TRIGGER monitor_verdicts_no_truncate BEFORE TRUNCATE ON tracelab\.monitor_verdicts\n"
                     r"\s+FOR EACH STATEMENT EXECUTE FUNCTION tracelab\.monitor_verdicts_guard\(\);", sql)
    assert re.findall(r"current_setting\('([^']*)'", sql) == ["tracelab.allow_purge"]      # the foundation hatch only


def test_an_evaluator_key_is_a_judge_id_or_a_code_check_with_its_version():
    m = re.search(r"evaluator_key\s+text NOT NULL CHECK \(evaluator_key ~ '([^']*)'\)", _sql())
    assert m
    key = re.compile(m.group(1))
    for ok in ("judge:0b1c2d3e-0000-4000-8000-000000000001", "code:no_answer@v1", "code:a1_b2@v12"):
        assert key.search(ok), ok
    for bad in ("judge:not-a-uuid", "judge:", "code:No_Answer@v1", "code:no answer@v1", "code:no_answer", "code:x@v",
                "code:1x@v1", "", "other:x@v1", "code:x@v1 "):
        assert not key.search(bad), bad
