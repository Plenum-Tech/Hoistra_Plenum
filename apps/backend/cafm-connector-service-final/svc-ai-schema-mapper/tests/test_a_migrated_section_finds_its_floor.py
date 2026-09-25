"""A migrated section finds its floor, and a meter placed on a floor finds the right section.

building_sections.floor_id is the real link to plenum_cafm.floors, and the writer never filled
it: every section arrived with floor_name text and floor_id NULL, so the join a floor view needs
(meter -> section -> floor) found nothing. The floors exist for every building before any
section is written; the name on the sheet is what they are called.

The second fault only appeared once a building had a section per floor. "Level 2" then names
two sections — the floor's own, and the server room that sits on Level 2 — and a meter placed
on "Level 2" resolved to neither. The section that IS called Level 2 is the one meant.

The module under test is pure; loaded by path so no service import chain is needed.
"""
import importlib.util
import os

import pytest

_HERE = os.path.dirname(__file__)
_NODES = os.path.join(_HERE, "..", "src", "graph", "nodes")
_SPEC = importlib.util.spec_from_file_location("meter_link", os.path.join(_NODES, "meter_link.py"))
ml = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ml)


class TestWhatNamesAFloor:
    @pytest.mark.parametrize("row,expected", [
        ({"floor_name": "Level 3"}, "Level 3"),
        ({"Floor": "Basement"}, "Basement"),
        ({"level": "7"}, "7"),
        ({"name": "Tenant floors", "section_type": "office"}, None),
    ])
    def test_the_floor_a_section_row_says_it_is_on(self, row, expected):
        assert ml.floor_hint(row) == expected

    def test_a_floor_is_looked_up_within_its_own_building_by_name_or_level(self):
        sql = ml.FLOOR_LOOKUP_SQL
        assert "f.building_id = CAST(:b AS uuid)" in sql
        assert "lower(f.name) = :k" in sql and "f.level::text = :k" in sql


class TestOneSectionOutOfSeveral:
    def test_a_single_hit_is_it(self):
        assert ml.pick_section([("sec-1", False)]) == "sec-1"

    def test_the_section_named_as_the_hint_wins_over_one_on_that_floor(self):
        hits = [("sec-server-room", False), ("sec-level-2", True)]
        assert ml.pick_section(hits) == "sec-level-2"

    def test_two_sections_on_the_floor_and_none_named_for_it_resolve_to_neither(self):
        assert ml.pick_section([("sec-plant", False), ("sec-car-park", False)]) is None

    def test_nothing_is_nothing(self):
        assert ml.pick_section([]) is None

    def test_the_lookup_reports_whether_the_name_matched(self):
        assert "(lower(s.name) = :k) AS by_name" in ml.SECTION_LOOKUP_SQL


class TestTheWriterUsesBoth:
    def test_sections_resolve_their_floor_and_meters_pick_by_name(self):
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        assert 'safe_table == "building_sections" and "floor_id" in db_cols' in src
        assert "_floor_for(" in src and "floor_hint(row)" in src
        assert "pick_section(_hit)" in src

    def test_a_section_row_is_not_placed_in_a_section(self):
        """section_id on building_sections is the row's own key. Resolving it from the floor the
        row names stamped another section's id on a new row (dropped as a conflict) or cached a
        miss that every meter after it then read: 11 of 12 floor sections, 2 of 24 meters placed."""
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        assert 'safe_table != "building_sections" and "section_id" in db_cols' in src

    def test_a_section_lookup_caches_hits_only(self):
        src = open(os.path.join(_NODES, "write_node.py"), encoding="utf-8").read()
        block = src[src.index("async def _section_for"):src.index("async def _floor_for")]
        assert "if _sid:" in block and "_section_cache[key] = _sid" in block
        assert "_section_cache[key] = pick_section" not in block
