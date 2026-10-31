"""0007_judge_floor.sql read as text (no database): it follows the migration file rules and only loosens the database
guard on a judge split's per-class floor from 10 to 1, so the code's floor (TRACE_LAB_JUDGE_EVAL_FLOOR, default 10) is
the one that decides. It creates and drops nothing else. Nothing here touches a database."""
from __future__ import annotations

from trace_lab import judges, migrate


def _floor():
    found = {f.version: f for f in migrate.files()}
    assert 7 in found and found[7].name == "0007_judge_floor.sql", sorted(found)
    return found[7]


def test_0007_follows_the_file_rules():
    assert migrate.lint(_floor()) == []
    first = [ln for ln in _floor().sql.splitlines() if ln.strip() and not ln.strip().startswith("--")][0]
    assert first == "SET LOCAL lock_timeout = '3s';"


def test_0007_only_swaps_the_split_floor_guard_from_10_to_1():
    code = "\n".join(ln for ln in _floor().sql.splitlines() if not ln.strip().startswith("--"))
    statements = [" ".join(s.split()) for s in code.split(";") if s.strip()]
    body = [s for s in statements if not s.upper().startswith("SET LOCAL")]
    assert body == [
        "ALTER TABLE tracelab.judge_splits DROP CONSTRAINT judge_splits_min_per_class_check",
        "ALTER TABLE tracelab.judge_splits ADD CONSTRAINT judge_splits_min_per_class_check CHECK (min_per_class >= 1)",
    ], body


def test_the_code_floor_stays_10_by_default_so_production_behaviour_is_unchanged():
    assert judges.MIN_PER_CLASS_EVAL >= 1
