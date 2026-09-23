"""A spreadsheet carries codes and names; the database keys on ids.

A CSV cannot carry a uuid anybody would type. It carries `asset_code`, `vendor_name`,
`contract_name` — and the columns they belong to are `asset_id`, `vendor_id`, `contract_id`.
Nothing bridged the two, so a file naming perfectly real things wrote null into every one.

For most columns that is a thin row. For ppm_visits it is an invisible one: the PPM health
query inner-joins assets, so a visit with no `asset_id` is dropped before anything is counted.
A database holding 264 of them reported "0 of 0 planned visits" — not a small number but no
number at all. A visit with no `contract_id` groups under "Unassigned", which hides the one
contract that is behind among all the rest.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os

import pytest

_HERE = os.path.dirname(__file__)
_NODES = os.path.join(_HERE, "..", "src", "graph", "nodes")
_SPEC = importlib.util.spec_from_file_location(
    "reference_link", os.path.join(_NODES, "reference_link.py"))
rl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rl)

ORG = "00000000-0000-0000-0000-000000000001"
CHILLER = "aaaa1111-2222-4333-8444-555566667777"
BOILER = "bbbb1111-2222-4333-8444-555566667777"


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class FakeDb:
    """Rows keyed by the value a file would name them with."""

    def __init__(self, rows):
        self.rows = rows            # [(id, *matchable values)]
        self.reads = 0

    async def fetch(self, sql, params):
        self.reads += 1
        k = params["k"]
        return [(r[0],) for r in self.rows
                if k in {str(v).lower() for v in r[1:] if v} or k == r[0]]


def resolver(db):
    return rl.ReferenceResolver(db.fetch, ORG, "plenum_cafm")


class TestWhatTheFileCallsIt:
    @pytest.mark.parametrize("column,row,expected", [
        ("asset_id", {"asset_code": "HP-CH-01"}, "HP-CH-01"),
        ("asset_id", {"Asset Code": "HP-CH-01"}, "HP-CH-01"),
        ("asset_id", {"equipment_code": "HP-CH-01"}, "HP-CH-01"),
        ("vendor_id", {"vendor_name": "Apex Mechanical Ltd"}, "Apex Mechanical Ltd"),
        ("vendor_id", {"Supplier": "Apex Mechanical Ltd"}, "Apex Mechanical Ltd"),
        ("contract_id", {"contract_name": "Lifts · UK"}, "Lifts · UK"),
        ("part_id", {"part_code": "PRT-AHU-BELT"}, "PRT-AHU-BELT"),
    ])
    def test_the_hint_is_read_however_the_column_is_spelled(self, column, row, expected):
        assert rl.hint_for(column, row) == expected

    def test_a_row_already_carrying_a_uuid_needs_no_resolution(self):
        assert rl.hint_for("asset_id", {"asset_id": CHILLER, "asset_code": "HP-CH-01"}) is None

    def test_a_code_under_our_own_header_is_still_a_hint(self):
        """`asset_id` holding "HP-CH-01" is the customer's id, not ours."""
        assert rl.hint_for("asset_id", {"asset_id": "HP-CH-01"}) == "HP-CH-01"

    def test_a_row_naming_nothing_returns_nothing(self):
        assert rl.hint_for("asset_id", {"scheduled_date": "2026-01-09"}) is None
        assert rl.hint_for("asset_id", None) is None

    def test_a_column_we_do_not_resolve_is_left_alone(self):
        assert rl.hint_for("building_id", {"building_code": "B-101"}) is None


class TestResolving:
    def test_a_code_finds_its_asset(self):
        db = FakeDb([(CHILLER, "HP-CH-01"), (BOILER, "HP-BLR-01")])
        assert run(resolver(db).resolve("asset_id", "HP-CH-01")) == CHILLER

    def test_a_name_finds_its_vendor(self):
        db = FakeDb([(CHILLER, "Apex Mechanical Ltd", "V-003")])
        assert run(resolver(db).resolve("vendor_id", "Apex Mechanical Ltd")) == CHILLER

    def test_matching_ignores_case(self):
        db = FakeDb([(CHILLER, "HP-CH-01")])
        assert run(resolver(db).resolve("asset_id", "hp-ch-01")) == CHILLER

    def test_a_year_of_visits_against_one_asset_costs_one_read(self):
        db = FakeDb([(CHILLER, "HP-CH-01")])
        r = resolver(db)
        for _ in range(200):
            assert run(r.resolve("asset_id", "HP-CH-01")) == CHILLER
        assert db.reads == 1

    def test_a_code_that_matches_nothing_resolves_to_nothing_and_is_reported(self):
        """Left absent rather than guessed. A name written into a uuid column fails the row;
        a wrong id is worse, because it succeeds."""
        db = FakeDb([(CHILLER, "HP-CH-01")])
        r = resolver(db)
        assert run(r.resolve("asset_id", "HP-CH-99")) is None
        assert r.report()["unresolved"]["asset_id"] == ["HP-CH-99"]

    def test_a_name_matching_two_rows_resolves_to_neither(self):
        """Choosing one would put a year of visits on the wrong contract."""
        db = FakeDb([(CHILLER, "Mechanical"), (BOILER, "Mechanical")])
        r = resolver(db)
        assert run(r.resolve("contract_id", "Mechanical")) is None
        assert r.report()["ambiguous"] == ["contract_id=Mechanical"]

    def test_nothing_at_all_resolves_to_nothing(self):
        db = FakeDb([(CHILLER, "HP-CH-01")])
        r = resolver(db)
        assert run(r.resolve("asset_id", None)) is None
        assert run(r.resolve("asset_id", "   ")) is None
        assert db.reads == 0

    def test_a_column_outside_the_map_is_refused_rather_than_guessed_at(self):
        db = FakeDb([(CHILLER, "anything")])
        assert run(resolver(db).resolve("building_id", "B-101")) is None
        assert db.reads == 0


class TestTheWriterUsesIt:
    @staticmethod
    def _source():
        return open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()

    def test_every_reference_column_is_resolved(self):
        src = self._source()
        assert "for _ref_col in REFERENCES:" in src
        assert "_refs.resolve(_ref_col, _rh)" in src

    def test_a_column_the_table_has_not_got_is_skipped(self):
        assert "if _ref_col not in db_cols:" in self._source()

    def test_an_unresolved_reference_is_dropped_rather_than_written_as_a_name(self):
        src = self._source()
        assert "safe_row.pop(_ref_col, None)" in src

    def test_the_run_reports_what_it_could_not_place(self):
        src = self._source()
        assert '"references": _refs.report(),' in src

    def test_the_four_columns_that_matter_are_covered(self):
        assert set(rl.REFERENCES) == {"asset_id", "vendor_id", "contract_id", "part_id"}
