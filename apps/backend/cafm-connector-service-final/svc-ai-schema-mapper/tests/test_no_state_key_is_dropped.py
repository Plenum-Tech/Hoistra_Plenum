"""A key a node writes must be declared, or the next step never sees it.

The migration graph is `StateGraph(MigrationState)` compiled with `interrupt_after` on every
step node, so a checkpoint is written after each step and LangGraph keeps only the keys the
schema declares. Anything else is dropped silently at the boundary — no error, no log, just a
value that is there when one node writes it and gone when the next one looks.

It has happened twice. In October the column-mapping gate's answers went to undeclared keys and
every "map this column to X" was ignored. On 29 Oct another eighteen were found: step 7 built
the containment hierarchy and step 8 read nothing; the hierarchy confirmation and the person's
own corrections went the same way; step 9 wrote the artefact URLs and step 10 stored None.

Both times the fault was invisible from the outside and expensive to find. This test makes the
third time a failing build instead.
"""
from __future__ import annotations

import os
import re

NODES = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes")
STATE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "state.py")

ASSIGNMENT = re.compile(r'state\[\s*"([a-z_][a-z0-9_]*)"\s*\]\s*=')
DECLARATION = re.compile(r"\s{4}([a-z_][a-z0-9_]*)\s*:")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def declared_keys() -> set[str]:
    return {m.group(1) for m in DECLARATION.finditer(_read(STATE))}


def migration_nodes() -> list[str]:
    """The node files that run on MigrationState — the ten-step pipeline.

    The schema-mapping and Fiix graphs have their own state classes, so their keys are checked
    against those, not this one.
    """
    out = []
    for name in sorted(os.listdir(NODES)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(NODES, name)
        if "MigrationState" in _read(path):
            out.append(path)
    return out


def test_every_key_a_migration_node_writes_is_declared():
    declared = declared_keys()
    undeclared: dict[str, str] = {}
    for path in migration_nodes():
        for lineno, line in enumerate(_read(path).splitlines(), 1):
            for m in ASSIGNMENT.finditer(line):
                key = m.group(1)
                if key not in declared:
                    undeclared.setdefault(key, f"{os.path.basename(path)}:{lineno}")
    assert not undeclared, (
        "these keys are written but not declared in MigrationState, so a checkpoint drops them "
        "before the next step reads them — declare them in src/graph/state.py: "
        + ", ".join(f"{k} ({where})" for k, where in sorted(undeclared.items()))
    )


def test_the_keys_that_were_lost_stay_declared():
    """The eighteen found on 29 Oct, named so a tidy-up cannot quietly remove them again."""
    declared = declared_keys()
    for key in (
        "containment_hierarchy", "hierarchy_levels", "hierarchy_duplicate_groups",
        "single_table_hierarchy_mode", "import_table_name", "import_table_plenum_role",
        "plenum_default_structure", "hierarchy_confirmed", "hierarchy_corrections",
        "plenum_default_hierarchy_accepted", "el_m7_error", "output_structure_md_url",
        "exported_artefacts", "overall_summary", "json_mapper", "mapping_source",
        "tier1_mappings", "execution_logs",
    ):
        assert key in declared, f"{key} was dropped at every step boundary once; keep it declared"


def test_the_pipeline_really_is_checkpointed_after_every_step():
    """The premise of this file: if the graph stopped interrupting, the risk would change shape."""
    graph = _read(os.path.join(os.path.dirname(STATE), "migration_graph.py"))
    assert "StateGraph(MigrationState)" in graph
    assert "interrupt_after=STEP_NODES" in graph
