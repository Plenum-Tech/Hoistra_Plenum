"""COALESCE across alias columns whose types differ.

`fetch_completed_work_orders_from_udr` COALESCEs each field over the aliases a tenant might
have — `actual_cost` or `cost_actual`, `estimated_cost` or `cost_estimated`. On hoistra_test
the migration wrote the workbook's costs into the VARCHAR aliases and left the NUMERIC ones
null, so the query became `COALESCE(numeric, character varying)`. Postgres refuses that
outright:

    DatatypeMismatchError: COALESCE types numeric and character varying cannot be matched

The failure was caught and logged as a warning, the fetch returned no work orders, and every
downstream step then behaved exactly as it would for a vendor with nothing to score. The
endpoint answered 200, `vendor_wo_scores` and `vendor_monthly_scorecards` stayed empty, and
the Vendors page read "not scored" for every vendor with no indication anything had broken.
Pressing Rebuild scorecards could never have worked, whatever the contracts said.

`_coalesce` already knows to cast each branch rather than the whole expression — its own
docstring says so. The callers for the cost fields simply never passed a cast.
"""
from __future__ import annotations

import re

from src.engines.contract_performance.scoring import _udr_select_parts


#: The real shape of plenum_cafm.work_orders on hoistra_test, read from
#: information_schema on 24 Sep 2026. The cost pairs are the ones that disagree.
REAL_WO_COLUMNS = {
    "id": "uuid",
    "wo_code": "character varying",
    "status": "character varying",
    "priority": "character varying",
    "reported_at": "timestamp with time zone",
    "attended_at": "timestamp with time zone",
    "completed_at": "timestamp with time zone",
    "first_fix": "boolean",
    "recall": "boolean",
    "return_visit": "boolean",
    "actual_cost": "numeric",
    "cost_actual": "character varying",
    "estimated_cost": "numeric",
    "cost_estimated": "character varying",
    "asset_id": "uuid",
    "vendor_id": "uuid",
    "organization_id": "uuid",
    "parts_cost": "numeric",
    "labour_hours": "numeric",
    "actual_hours": "numeric",
}

_BRANCH = re.compile(r"wo\.([a-z_]+)(::[a-z ]+)?")


def _coalesce_branches(part: str) -> list[tuple[str, str | None]]:
    """(column, cast) for each branch of a COALESCE, or [] when it is not one."""
    inner = re.match(r"COALESCE\((.*?)\)\s+AS\s+\w+$", part.strip(), re.S)
    if not inner:
        return []
    return [(m.group(1), m.group(2)) for m in _BRANCH.finditer(inner.group(1))]


def test_no_coalesce_mixes_two_column_types_without_casting_them():
    for part in _udr_select_parts(REAL_WO_COLUMNS):
        branches = _coalesce_branches(part)
        if len(branches) < 2:
            continue
        types = {REAL_WO_COLUMNS[c] for c, _ in branches if c in REAL_WO_COLUMNS}
        if len(types) < 2:
            continue
        uncast = [c for c, cast in branches if cast is None]
        assert not uncast, (
            f"{part!r} coalesces columns of differing types {sorted(types)} "
            f"with no cast on {uncast} — Postgres rejects the whole statement"
        )


def test_the_cost_pair_that_broke_scoring_is_cast():
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _udr_select_parts(REAL_WO_COLUMNS)}
    assert "::numeric" in parts["actual_cost"]
    assert "::numeric" in parts["estimated_cost"]


def test_a_tenant_with_only_one_alias_is_unaffected():
    """Nothing to reconcile when a tenant has just one of the pair."""
    cols = {"completed_at": "timestamp with time zone", "cost_actual": "character varying"}
    parts = {p.rsplit(" AS ", 1)[1]: p for p in _udr_select_parts(cols)}
    assert "COALESCE" not in parts["actual_cost"]
    assert "wo.cost_actual" in parts["actual_cost"]
