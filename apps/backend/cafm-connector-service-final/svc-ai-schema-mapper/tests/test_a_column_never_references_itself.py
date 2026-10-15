"""A column never references itself, and a tree with a loop in it is drawn, not recursed into.

Migration 07c03a98 (24 Sep 2026, the floor sub-meter workbook) stopped at Node 7 with
"maximum recursion depth exceeded". Node 6 had accepted Energy_Meters.meter_ref ->
Energy_Meters.meter_ref from Claude: the validator checks that the source values appear among
the target values, and a column's values always appear among its own, so it validated at 100%.
Every meter became its own parent (0 roots in 34 records) and the renderer that draws the
containment tree followed Energy_Meters -> Energy_Meters until Python stopped it.

The modules under test are loaded by path so no service import chain is needed.
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(__file__)
_SRC = os.path.join(_HERE, "..", "src")

# tree_resolver logs through cafm_shared, which the host running these tests need not have.
if "cafm_shared" not in sys.modules:
    import logging as _std_logging
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: _std_logging.getLogger(name or "cafm_shared")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_SRC, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source(rel):
    return open(os.path.join(_SRC, rel), encoding="utf-8").read()


def _function_from_source(rel, name):
    """Extract one top-level function from a module whose imports need the service."""
    import ast
    src = _source(rel)
    tree = ast.parse(src)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    mod = types.ModuleType(f"extracted_{name}")
    exec(compile(ast.Module(body=[node], type_ignores=[]), rel, "exec"), mod.__dict__)
    return getattr(mod, name)


METERS = [{"meter_ref": f"NB-B-101-E-L{i:02d}", "building_code": "B-101"} for i in range(1, 6)]


class TestTheValidatorRefusesASelfReference:
    def test_a_column_proposed_as_its_own_parent_is_discarded(self):
        validate = _function_from_source("graph/nodes/hierarchy_node.py", "_validate_inferred_fk_dicts")
        said = []
        out = validate(
            [{"source_table": "Energy_Meters", "source_column": "meter_ref",
              "target_table": "Energy_Meters", "target_column": "meter_ref"}],
            {"Energy_Meters": METERS}, said.append,
        )
        assert out == []
        assert any("cannot reference itself" in m for m in said)

    def test_a_real_reference_between_tables_still_validates(self):
        validate = _function_from_source("graph/nodes/hierarchy_node.py", "_validate_inferred_fk_dicts")
        readings = [{"meter_ref": m["meter_ref"], "consumption_kwh": 1.0} for m in METERS]
        out = validate(
            [{"source_table": "Meter_Readings", "source_column": "meter_ref",
              "target_table": "Energy_Meters", "target_column": "meter_ref"}],
            {"Energy_Meters": METERS, "Meter_Readings": readings}, lambda m: None,
        )
        assert len(out) == 1 and out[0]["data_match_rate"] == 1.0

    def test_a_genuine_parent_column_in_the_same_table_is_still_allowed(self):
        validate = _function_from_source("graph/nodes/hierarchy_node.py", "_validate_inferred_fk_dicts")
        locs = [{"location_id": "L1", "parent_id": None}, {"location_id": "L2", "parent_id": "L1"}]
        out = validate(
            [{"source_table": "Locations", "source_column": "parent_id",
              "target_table": "Locations", "target_column": "location_id"}],
            {"Locations": locs}, lambda m: None,
        )
        assert len(out) == 1


class TestTheTreePictureSurvivesALoop:
    def test_a_table_that_is_its_own_child_is_drawn_once(self):
        render = _function_from_source("graph/nodes/verify_hierarchy_node.py", "_render_tree_visual")
        out = render({"Energy_Meters": {"children": ["Energy_Meters"]}})
        assert out.count("Energy_Meters") == 2 and "loop" in out

    def test_a_loop_between_two_tables_terminates(self):
        render = _function_from_source("graph/nodes/verify_hierarchy_node.py", "_render_tree_visual")
        out = render({"A": {"children": ["B"]}, "B": {"children": ["A"]}})
        assert "loop" in out and len(out.splitlines()) <= 6

    def test_an_ordinary_tree_is_unchanged(self):
        render = _function_from_source("graph/nodes/verify_hierarchy_node.py", "_render_tree_visual")
        out = render({"Sites": {"children": ["Buildings"]}, "Buildings": {"children": ["Assets"]},
                      "Assets": {"children": []}})
        assert out.splitlines() == ["└── Sites", "    └── Buildings", "        └── Assets"]


class TestARecordThatIsItsOwnParentIsARoot:
    def test_every_row_pointing_at_itself_gives_every_row_as_a_root(self):
        tr = _load("tree_resolver", "hierarchy/tree_resolver.py")
        trees = tr.resolve_self_referencing_trees(
            {"Energy_Meters": METERS},
            [{"source_table": "Energy_Meters", "source_column": "meter_ref",
              "target_column": "meter_ref"}],
        )
        assert len(trees["Energy_Meters"]) == len(METERS)
