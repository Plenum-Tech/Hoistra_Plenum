"""0003_review.sql read as text (no database): it follows the migration file rules, creates exactly the four
review tables and the supersede-only guard in schema tracelab, keeps the names later code catches, and
its CHECK lists are the Phase 4 contract. Nothing here touches a database."""
from __future__ import annotations

import re

from trace_lab import migrate


def _review():
    found = {f.version: f for f in migrate.files()}
    assert 3 in found and found[3].name == "0003_review.sql", sorted(found)
    return found[3]


def _check_list(sql: str, column: str) -> list[str]:
    m = re.search(rf"CHECK \({column} IN \(([^)]*)\)\)", sql)
    assert m, column
    return [v.strip().strip("'") for v in m.group(1).split(",")]


def _table(sql: str, name: str) -> str:
    m = re.search(rf"CREATE TABLE tracelab\.{name} \((.*?)\n\);", sql, re.S)
    assert m, name
    return m.group(1)


def test_the_file_is_third_and_follows_the_file_rules():
    f = _review()
    assert migrate.lint(f) == []
    assert migrate.split_statements(f.sql)[0] == migrate.FIRST_STATEMENT


def test_it_creates_the_four_review_tables_and_the_guard_and_nothing_outside_tracelab():
    sql = _review().sql
    stmts = migrate.split_statements(sql)
    tables = [m.group(1) for s in stmts if (m := re.match(r"^CREATE TABLE (\S+)", s))]
    assert tables == ["tracelab.sample_batches", "tracelab.sample_items", "tracelab.annotations",
                      "tracelab.annotation_events"]
    functions = [m.group(1) for s in stmts if (m := re.match(r"^CREATE FUNCTION (\S+?)\(", s))]
    assert functions == ["tracelab.supersede_only"]            # 0001 only defines refuse_change
    for old in ("plenum_cafm", "plenum_tracelab", "turn_meta"):
        assert old not in sql, old


def test_the_names_later_code_catches_are_present():
    sql = _review().sql
    for name in ("ux_tl_items_turn", "ux_tl_items_position", "ux_tl_ann_verdict_head", "ux_tl_ann_supersedes",
                 "ux_tl_ann_lineage_head", "ck_tl_batches_prevalence", "ck_tl_batches_window", "ck_tl_ann_wrong",
                 "ck_tl_ann_right"):
        assert re.search(rf"\b{name}\b", sql), name


def test_the_check_lists_are_the_phase_4_contract():
    sql = _review().sql
    assert _check_list(sql, "plan_step") == ["initial_uniform", "cluster_representatives", "dimension",
                                             "depth_search", "final_uniform", "extra"]
    assert _check_list(sql, "strategy") == ["uniform", "diversity", "dimension", "outlier", "enrich", "monitor_flagged"]
    assert _check_list(sql, "kind") == ["verdict", "comment"]
    assert _check_list(sql, "verdict") == ["right", "wrong"]
    assert _check_list(sql, "action") == ["create", "edit", "delete", "reveal", "dispose", "undispose"]
    assert "CHECK (requested BETWEEN 1 AND 200)" in sql and "CHECK (reviews_required BETWEEN 1 AND 3)" in sql
    assert "CHECK (counts_for_prevalence = (strategy = 'uniform'))" in sql.replace("\n", " ").replace("  ", " ")


def test_history_tables_are_append_only_and_annotations_supersede_only():
    sql = _review().sql
    for table in ("sample_items", "annotation_events"):
        assert re.search(rf"BEFORE UPDATE OR DELETE ON tracelab\.{table}\s+FOR EACH ROW EXECUTE FUNCTION "
                         r"tracelab\.refuse_change\(\)", sql), table
        assert re.search(rf"BEFORE TRUNCATE ON tracelab\.{table}\s+FOR EACH STATEMENT EXECUTE FUNCTION "
                         r"tracelab\.refuse_change\(\)", sql), table
    assert re.search(r"BEFORE UPDATE OR DELETE ON tracelab\.annotations\s+FOR EACH ROW EXECUTE FUNCTION "
                     r"tracelab\.supersede_only\(\)", sql)
    assert re.search(r"BEFORE TRUNCATE ON tracelab\.annotations\s+FOR EACH STATEMENT EXECUTE FUNCTION "
                     r"tracelab\.supersede_only\(\)", sql)


def test_a_turn_id_is_a_plain_text_column_with_no_foreign_key_into_the_chat():
    sql = _review().sql
    for name in ("sample_items", "annotations", "annotation_events"):
        body = _table(sql, name)
        assert re.search(r"^\s*turn_id\s+text\b", body, re.M), name
        assert "REFERENCES" not in re.search(r"^\s*turn_id[^\n]*", body, re.M).group(0), name
    refs = re.findall(r"REFERENCES\s+([\w.]+)", sql)
    assert sorted(refs) == ["tracelab.annotations", "tracelab.sample_batches"]       # only within the review tables
