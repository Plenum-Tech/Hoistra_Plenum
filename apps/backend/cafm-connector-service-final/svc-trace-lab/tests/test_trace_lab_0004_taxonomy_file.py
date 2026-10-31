"""0004_taxonomy.sql read as text (no database): it follows the migration file rules, creates exactly the six taxonomy
tables in schema tracelab, accepts the data level, records label provenance, lets a company admin propose a failure
mode, and uses the foundation guards. Nothing here touches a database."""
from __future__ import annotations

import re

from trace_lab import jobs, migrate


def _taxonomy():
    found = {f.version: f for f in migrate.files()}
    assert 4 in found and found[4].name == "0004_taxonomy.sql", sorted(found)
    return found[4]


def _sql() -> str:
    return _taxonomy().sql


def _table(sql: str, name: str) -> str:
    m = re.search(rf"CREATE TABLE tracelab\.{name} \((.*?)\n\);", sql, re.S)
    assert m, name
    return m.group(1)


def _check_list(body: str, column: str) -> list[str]:
    m = re.search(rf"CHECK \({column} IN \(([^)]*)\)\)", body)
    assert m, column
    return [v.strip().strip("'") for v in re.sub(r"\s+", " ", m.group(1)).split(",")]


def test_the_file_is_fourth_follows_the_file_rules_and_starts_with_the_lock_timeout():
    f = _taxonomy()
    assert migrate.lint(f) == []
    assert migrate.split_statements(f.sql)[0] == migrate.FIRST_STATEMENT


def test_it_creates_the_six_taxonomy_tables_and_nothing_outside_tracelab():
    sql = _sql()
    stmts = migrate.split_statements(sql)
    tables = [m.group(1) for s in stmts if (m := re.match(r"^CREATE TABLE (\S+)", s))]
    assert tables == ["tracelab.modes", "tracelab.mode_events", "tracelab.requirements", "tracelab.proposal_runs",
                      "tracelab.proposals", "tracelab.labels"]
    assert not [s for s in stmts if re.match(r"^CREATE (OR REPLACE )?FUNCTION", s)]   # the guards are 0001 and 0003
    for old in ("plenum_cafm", "plenum_tracelab", "turn_meta"):
        assert old not in sql, old
    assert "IF NOT EXISTS" not in sql.upper()
    assert not re.search(r"\b(GRANT|REVOKE|CONCURRENTLY)\b", sql, re.I)


def test_nothing_in_it_crosses_into_the_application_database():
    """No foreign key to a chat table: a turn is named by id only, and its evidence is copied by snapshot."""
    refs = re.findall(r"REFERENCES\s+(\S+)", _sql())
    assert refs and all(r.startswith("tracelab.") for r in refs)


def test_modes_and_requirements_accept_the_data_level():
    sql = _sql()
    assert sql.count("level IN ('technical','experience','domain','data')") == 2
    assert "level" in _table(sql, "modes") and "level" in _table(sql, "requirements")


def test_a_mode_can_be_proposed_and_rejected_with_a_reason():
    modes = _table(_sql(), "modes")
    assert _check_list(modes, "status") == ["proposed", "candidate", "confirmed", "frozen", "retired", "rejected"]
    for column in ("proposer_organization_id", "rejected_reason", "decided_by", "decided_at"):
        assert re.search(rf"^\s+{column}\s", modes, re.M), column
    assert "ck_tl_modes_proposer" in modes and "ck_tl_modes_rejected" in modes
    assert "ux_tl_modes_live_name ON tracelab.modes (name) WHERE status NOT IN ('retired','rejected')" in _sql()


def test_mode_events_name_the_proposal_steps():
    events = _table(_sql(), "mode_events")
    assert _check_list(events, "action") == ["create", "edit", "rename", "merge", "split", "retire", "restore",
                                             "propose", "accept", "reject"]


def test_labels_record_source_and_approval():
    sql = _sql()
    labels = _table(sql, "labels")
    assert _check_list(labels, "source") == ["human_structured_label", "accepted_suggestion", "rejected_suggestion",
                                             "accepted_label_proposal", "human_open_code"]
    assert "approval          text NOT NULL DEFAULT 'single' CHECK (approval IN ('single','bulk'))" in sql
    assert "ck_tl_labels_bulk" in sql


def test_history_tables_use_the_foundation_guards():
    sql = _sql()
    assert "EXECUTE FUNCTION tracelab.refuse_change()" in sql
    assert "EXECUTE FUNCTION tracelab.supersede_only()" in sql
    assert "refuse_update_delete" not in sql and "versions_guard" not in sql
    for trigger, table in (("mode_events", "mode_events"), ("labels", "labels")):
        assert f"BEFORE UPDATE OR DELETE ON tracelab.{table}" in sql
        assert f"BEFORE TRUNCATE ON tracelab.{table}" in sql


def test_every_turn_column_has_an_index_that_leads_with_it():
    sql = _sql()
    assert "CREATE INDEX ix_tl_labels_turn ON tracelab.labels (turn_id);" in sql
    assert "CREATE INDEX ix_tl_prop_turn ON tracelab.proposals (turn_id) WHERE turn_id IS NOT NULL;" in sql


def test_the_splitter_reads_every_statement():
    stmts = migrate.split_statements(_sql())
    assert stmts[0] == "SET LOCAL lock_timeout = '3s'"
    assert all(s.strip().split()[0].upper() in ("SET", "CREATE", "COMMENT") for s in stmts)


def test_the_proposal_run_kinds_are_the_job_kinds_that_spend():
    runs = _table(_sql(), "proposal_runs")
    kinds = _check_list(runs, "kind")
    assert kinds == ["grouping", "depth_scan", "cells"]
    assert {f"proposal_{k}" for k in kinds} == {k for k in jobs.KINDS if k.startswith("proposal_")}
