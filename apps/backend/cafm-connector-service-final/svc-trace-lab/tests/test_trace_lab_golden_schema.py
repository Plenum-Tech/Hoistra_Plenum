"""0002_golden.sql read as text (no database): it follows the migration file rules, creates exactly the five
golden tables in schema tracelab, and its CHECK lists are the Phase 3 contract (interfaces.md) word for word."""
from __future__ import annotations

import re

from trace_lab import jobs, migrate


def _golden():
    found = {f.version: f for f in migrate.files()}
    assert 2 in found and found[2].name == "0002_golden.sql", sorted(found)
    return found[2]


def _check_list(sql: str, column: str) -> list[str]:
    m = re.search(rf"CHECK \({column} IN \(([^)]*)\)\)", sql)
    assert m, column
    return [v.strip().strip("'") for v in m.group(1).split(",")]


def test_the_file_is_second_and_follows_the_file_rules():
    f = _golden()
    assert migrate.lint(f) == []
    assert migrate.split_statements(f.sql)[0] == migrate.FIRST_STATEMENT


def test_it_creates_the_five_golden_tables_and_nothing_outside_tracelab():
    stmts = migrate.split_statements(_golden().sql)
    tables = [m.group(1) for s in stmts if (m := re.match(r"^CREATE TABLE (\S+)", s))]
    assert tables == ["tracelab.golden_sets", "tracelab.golden_cases", "tracelab.golden_runs",
                      "tracelab.golden_results", "tracelab.golden_reveals"]
    assert "plenum_cafm" not in _golden().sql


def test_the_check_lists_are_the_phase_3_contract():
    sql = _golden().sql
    assert _check_list(sql, "kind") == ["scenario", "regression"]
    assert _check_list(sql, "status")[:2] == ["active", "retired"]
    assert _check_list(sql, "evaluation") == ["objective", "judgment"]
    assert _check_list(sql, "purpose") == ["adhoc", "baseline", "release_check", "repeat"]
    assert _check_list(sql, "outcome") == ["pending", "pass", "fail", "unsure", "degraded", "stale_reference", "error"]
    assert "CHECK (k BETWEEN 1 AND 20)" in sql and "CHECK (rep BETWEEN 1 AND 20)" in sql


def test_a_run_has_the_same_statuses_as_a_job():
    sql = _golden().sql
    m = re.search(r"CREATE TABLE tracelab\.golden_runs .*?CHECK \(status IN \(([^)]*)\)\)", sql, re.S)
    assert tuple(v.strip().strip("'") for v in m.group(1).split(",")) == jobs.STATUSES


def test_golden_cases_and_reveals_are_append_only():
    sql = _golden().sql
    for table in ("golden_cases", "golden_reveals"):
        assert f"BEFORE UPDATE OR DELETE ON tracelab.{table}" in sql
        assert f"BEFORE TRUNCATE ON tracelab.{table}" in sql
