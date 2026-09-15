"""Every branch of the drawer can name its rows.

Riverside Court's graph rendered its two meters as `732ecf5c-d421-51c6-…` and
`e17f0ac8-cb13-59f0-…`. The meters branch offered ("meter_ref", "name", "mpan", "mprn") as
label candidates and plenum_cafm.meters has none of them — it has `mpan_mprn`, one column
holding either number, because a meter has an MPAN (electricity) or an MPRN (gas) and never
both. No candidate matched, label_expr became NULL, and the row fell through to its uuid:
the single identifier a person reading the drawer cannot use for anything.

Nothing failed. A branch with no usable label is indistinguishable at every layer from a
branch whose rows genuinely have no name, so it renders, returns 200, and looks like data.

These tests state the column sets the live tables actually have and check that each branch
can name a row in one. They are a copy of the schema and will drift from it — that is the
trade for catching this without a database, and a drifting copy that fails loudly beats a
silent uuid.
"""
from __future__ import annotations

import pytest

from src.engines.energy.building_tree import _BRANCHES, _LINK

#: Columns each branch table carries, restricted to the ones a label, detail or doc_ref
#: names. Generated from information_schema on the test portfolio rather than written by
#: hand — the first hand-written version claimed equipment had an `equipment_code` and it
#: does not, which is the same class of mistake these tests exist to catch.
LIVE_COLUMNS: dict[str, set[str]] = {
    "assets": {"asset_name", "asset_code", "status", "building_id"},
    "compliance_certificates": {"certificate_type_code", "certificate_number",
                                "expiry_date", "status", "document_id",
                                "source_document_id", "building_id"},
    "contracts": {"contract_ref", "status", "start_date", "document_id", "building_id"},
    "documents": {"file_name", "title", "doc_type", "building_id"},
    # No building_id: this table cannot be joined on the tree's link at all, and the tree
    # reports it under `unavailable` rather than as an empty branch.
    "equipment": {"name"},
    "floors": {"name", "level", "gross_area_sqft", "building_id"},
    "invoices": {"invoice_ref", "amount", "status", "document_id", "building_id"},
    "meters": {"mpan_mprn", "meter_type", "unit", "building_id"},
    "spaces": {"name", "space_type", "gross_area_sqft", "building_id"},
    "work_orders": {"title", "wo_code", "status", "building_id"},
}


def label_candidates(table: str) -> tuple[str, ...]:
    return tuple(_BRANCHES[table].get("label") or ())


@pytest.mark.parametrize("table", sorted(_BRANCHES))
def test_every_branch_offers_at_least_one_label_candidate(table: str):
    assert label_candidates(table), f"{table} declares no label column at all"


@pytest.mark.parametrize("table", sorted(_BRANCHES))
def test_every_branch_can_name_a_row_in_the_live_table(table: str):
    # The regression. A branch whose candidates miss every column the table has renders
    # uuids and reports success.
    cols = LIVE_COLUMNS[table]
    usable = [c for c in label_candidates(table) if c in cols]
    assert usable, (
        f"{table} labels on {label_candidates(table)}, and the live table has none of "
        f"them — every row will render as its raw id. Live columns: {sorted(cols)}"
    )


def test_meters_label_on_the_column_that_holds_the_number():
    # Named explicitly rather than left to the parametrised test above, because this is
    # the case that was broken and the one most likely to be re-broken by someone tidying
    # the tuple back down to mpan/mprn.
    assert "mpan_mprn" in label_candidates("meters")


def test_the_separate_spellings_are_kept_as_fallbacks():
    # Deployments predating the single column still have them, and dropping them would
    # trade one shape's uuids for another's.
    assert {"mpan", "mprn"} <= set(label_candidates("meters"))


@pytest.mark.parametrize("table", sorted(_BRANCHES))
def test_the_building_link_is_never_offered_as_a_label(table: str):
    # Every one of these rows has a building_id and it is the same value on all of them.
    # As a label it would name every row identically, which is worse than a uuid.
    assert _LINK not in label_candidates(table)


def test_the_column_map_covers_every_branch():
    # If a branch is added and this map is not extended, the tests above would silently
    # stop covering it with a KeyError rather than a clear failure.
    assert set(LIVE_COLUMNS) == set(_BRANCHES), (
        f"missing from LIVE_COLUMNS: {sorted(set(_BRANCHES) - set(LIVE_COLUMNS))}"
    )
