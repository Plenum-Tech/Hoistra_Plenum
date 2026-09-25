"""A row that names a parent is written after that parent exists, and asks again if it moved.

Ingesting the Harbour Point workbook on 23 Sep 2026 produced pages full of "Unassigned": 13 work
orders and 64 PPM visits with a null vendor_id, every PPM visit with a null contract_id, and no
building sections at all. Nothing was missing from the file — the vendors, the contracts and the
sections were all in it and all landed. Three separate faults conspired.

ORDER. Node 9 wrote tables in the order the sheets happened to be arranged, and hierarchy
detection reads the FILE, so it only finds relationships the file makes obvious. work_orders came
before vendors and ppm_visits before vendor_contracts, so those references resolved against tables
that were still empty. The parent relationships between plenum_cafm's own core tables are facts
about the schema, not about the file, so they are stated here rather than inferred.

CACHED MISSES. ReferenceResolver lives for the whole run and cached "not found" answers. Even once
the order was right, the first table to ask for a vendor poisoned the answer for every table after
it. Same defect as MeterResolver.find had, one level up.

A NOT NULL PARENT NOBODY RESOLVED. building_sections.building_id is NOT NULL and the table was not
in _BUILDING_LINKED_TABLES, so its building_code was never turned into a building_id and all five
rows were rejected. With no sections, assets had nothing to hang off and the Assets page reported
"Location not set" for all twelve.
"""
from __future__ import annotations

import logging
import sys
import types

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import reference_link as rl  # noqa: E402
from src.graph.nodes import write_node as wn  # noqa: E402

WN_SRC = open(wn.__file__, encoding="utf-8").read()
RL_SRC = open(rl.__file__, encoding="utf-8").read()


def _core_parents() -> dict:
    """The literal out of the writer, without running the node."""
    import ast
    tree = ast.parse(WN_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_CORE_PARENTS":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_CORE_PARENTS":
            return ast.literal_eval(node.value)
    raise AssertionError("_CORE_PARENTS not found")


class TestASectionKnowsItsBuilding:
    def test_building_sections_is_building_linked(self):
        assert "building_sections" in wn._BUILDING_LINKED_TABLES, \
            "building_id is NOT NULL there; unresolved, every section row is rejected"

    def test_the_tables_that_were_already_linked_still_are(self):
        for t in ("assets", "work_orders", "energy_meters"):
            assert t in wn._BUILDING_LINKED_TABLES


class TestParentsAreWrittenFirst:
    def test_the_relationships_that_matter_are_stated_not_inferred(self):
        parents = _core_parents()
        assert "vendors" in parents["work_orders"]
        assert "vendors" in parents["vendor_contracts"]
        assert "vendor_contracts" in parents["ppm_visits"]
        assert "buildings" in parents["building_sections"]
        assert "building_sections" in parents["assets"], \
            "a section must exist before an asset can be placed in one"
        assert "energy_meters" in parents["meter_readings"]

    def test_no_table_is_its_own_parent(self):
        for child, parents in _core_parents().items():
            assert child not in parents

    def test_the_order_has_no_cycle(self):
        """A cycle would make the topological pass drop a table silently."""
        parents = _core_parents()
        seen, stack = set(), set()

        def visit(t):
            if t in seen:
                return
            assert t not in stack, f"cycle through {t}"
            stack.add(t)
            for p in parents.get(t, ()):
                visit(p)
            stack.discard(t)
            seen.add(t)

        for t in parents:
            visit(t)

    def test_they_are_merged_into_the_detected_hierarchy_not_replacing_it(self):
        assert "_parents_of_dest.setdefault(_c, set()).update(_ps)" in WN_SRC, \
            "what the detector found about this file must still count"


class TestAMissIsNotRemembered:
    def test_reference_lookups_cache_hits_only(self):
        assert "if found is not None:" in RL_SRC
        i = RL_SRC.index("if found is not None:")
        assert "self._cache[key] = found" in RL_SRC[i:i + 120]

    def test_there_is_no_unconditional_cache_write_left(self):
        for line in RL_SRC.splitlines():
            if line.strip() == "self._cache[key] = found":
                assert line.startswith("            "), \
                    "a cache write at the outer indent caches misses again"
