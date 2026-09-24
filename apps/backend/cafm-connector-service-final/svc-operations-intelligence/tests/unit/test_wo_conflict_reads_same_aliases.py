"""The conflict check must read the same columns the scoring fetch reads.

`detect_and_flag_wo_conflict` compares the values a scoring run carries against the values
stored on the work order. It selected only the canonical `actual_cost` / `estimated_cost`,
while `_udr_select_parts` COALESCEs those with the `cost_actual` / `cost_estimated` aliases.

On hoistra_test the migration filled the ALIASES and left the canonical columns null, so the
comparison was null-against-834.50 and `_differs` returns True the moment one side is null.
Both of Meridian's completed work orders were flagged `conflict_flag = true` and excluded —
and since the fetch also filters on `conflict_flag IS NOT TRUE`, they were then invisible to
every later run. The only vendor with a confirmed contract had nothing left to score, so the
Vendors page stayed empty with no error anywhere.

The file already carries a comment about the same class of bug in the timestamp fields:
"Comparing those as text made every work order conflict with itself on every scoring run."
This is that bug again, in the cost fields, caused by the two readers disagreeing about
where the value lives rather than about how to format it.
"""
from __future__ import annotations

from src.engines.contract_performance.conflicts import _conflict_select_parts, _differs


#: hoistra_test's real shape: canonical numeric columns present but empty, aliases populated.
REAL_WO_COLUMNS = {
    "actual_cost": "numeric",
    "cost_actual": "character varying",
    "estimated_cost": "numeric",
    "cost_estimated": "character varying",
    "attended_at": "timestamp with time zone",
    "completed_at": "timestamp with time zone",
}


def test_cost_fields_read_the_alias_columns_too():
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _conflict_select_parts(REAL_WO_COLUMNS)}
    assert "cost_actual" in parts["actual_cost"], (
        "the conflict check must look where the value actually is, not only in the "
        "canonical column the scoring fetch already knows may be empty"
    )
    assert "cost_estimated" in parts["estimated_cost"]


def test_cost_fields_cast_both_branches():
    """Same COALESCE type rule as the scoring fetch — Postgres rejects a mixed one."""
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _conflict_select_parts(REAL_WO_COLUMNS)}
    assert parts["actual_cost"].count("::numeric") >= 2


def test_timestamps_are_still_selected():
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _conflict_select_parts(REAL_WO_COLUMNS)}
    assert "attended_at" in parts and "completed_at" in parts


def test_a_tenant_without_the_aliases_still_gets_its_canonical_columns():
    cols = {"actual_cost": "numeric", "completed_at": "timestamp with time zone"}
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _conflict_select_parts(cols)}
    assert "wo.actual_cost" in parts["actual_cost"]
    assert "COALESCE" not in parts["actual_cost"]


def test_a_stored_value_equal_to_the_incoming_one_is_not_a_conflict():
    """Guards the actual regression: 834.50 against 834.5 must not flag."""
    assert _differs(None, 834.5) is True          # the broken comparison
    assert _differs(834.50, 834.5) is False       # the one it should have been making


def test_the_query_names_the_table_alias_the_parts_use():
    """_coalesce prefixes every column with `wo.`, so the FROM clause must declare it.

    The first version of this fix passed every unit test above and still failed against
    Postgres with `missing FROM-clause entry for table "wo"` — which, on this path, would
    have been swallowed exactly like the COALESCE error before it.
    """
    import inspect

    from src.engines.contract_performance import conflicts

    sql_text = inspect.getsource(conflicts.detect_and_flag_wo_conflict)
    assert "plenum_cafm.work_orders wo " in sql_text
    assert "wo.wo_code = :wc" in sql_text
