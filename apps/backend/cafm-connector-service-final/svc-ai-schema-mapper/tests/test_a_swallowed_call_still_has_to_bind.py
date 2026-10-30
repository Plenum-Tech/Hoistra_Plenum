"""A call wrapped in try/except must still match the function it calls.

On 30 Oct the classification gate applied the person's answer correctly — the service log says
so plainly:

    [Classification Gate] Applied — overrides_applied={'G23': 'fk_to_shared', 'G25': 'fk_to_shared'}
                                    lookup_tables_added=['schedule_id', 'alarm_id']
    [Classification Gate] could not record the confirmation:
        migration_append_node_log_auto() missing 1 required positional argument: 'logs'

The lookup tables were built. The record that *publishes* them to the page was not, because the
call omitted a required argument — and it sits inside `except Exception` so the run carried on
and nobody saw a failure. The gate's own payload is written before the person answers and
cleared on resume, so that record is the only post-answer source the page has: losing it left
the panel showing the list from before the click, which cannot contain the table just asked for.
Two rounds of hunting a missing alarm_id followed, both times for a table that existed.

pyflakes does not check call arity, and `except Exception` hides it at runtime. So bind every
call to these writers against the real signature here instead.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

NODES = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes")

#: Writers whose calls are routinely wrapped in try/except, so arity errors are invisible.
WATCHED = ("migration_append_node_log_auto",)


def _calls(func_name: str):
    """Every call site of `func_name` under src/graph/nodes, as (file, line, nargs, kwnames)."""
    out = []
    for name in sorted(os.listdir(NODES)):
        if not name.endswith(".py"):
            continue
        path = os.path.join(NODES, name)
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if func_name not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            called = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
            if called != func_name:
                continue
            if any(isinstance(a, ast.Starred) for a in node.args) or any(
                k.arg is None for k in node.keywords
            ):
                continue  # *args / **kwargs — nothing to check statically
            out.append((name, node.lineno, len(node.args),
                        tuple(k.arg for k in node.keywords)))
    return out


@pytest.mark.parametrize("func_name", WATCHED)
def test_every_call_site_binds_against_the_real_signature(func_name):
    from src.graph.nodes import schema_db_writer

    sig = inspect.signature(getattr(schema_db_writer, func_name))
    sites = _calls(func_name)
    assert sites, f"no call sites found for {func_name} — has it been renamed?"

    broken = []
    for (file, line, nargs, kwnames) in sites:
        try:
            sig.bind(*[object()] * nargs, **{k: object() for k in kwnames})
        except TypeError as exc:
            broken.append(f"{file}:{line} — {exc}")
    assert not broken, (
        f"these calls to {func_name} do not match its signature {sig}; each one raises at "
        "runtime inside an except-block, so the write is silently lost:\n  "
        + "\n  ".join(broken)
    )


def test_the_classification_confirmation_is_one_of_those_call_sites():
    """The specific record that failed — if it is ever dropped, the panel goes stale again."""
    with open(os.path.join(NODES, "grouping_review_node.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert '"Classification Confirmed"' in src
    assert "shared_attribute_tables" in src, (
        "the post-answer record must carry the lookup tables, including the ones the answer "
        "just created — it is the only post-answer source the page has")
