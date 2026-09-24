"""The UDR analysis runs on a thread, so the page can be told the migration finished.

Node 9 persists `status=complete` and logs `✓ MIGRATION COMPLETE`, and the panel went on saying
"Running · Node 8 of 9" under "Could not read the migration: timed out after 60s". The status
the page needed was already in the database. Nothing could serve it.

`run_udr_pipeline` is synchronous and was called straight from `async def udr_node`, so it ran
ON the event loop. Its `prefix_columns` stage took 69,563ms on the 17-sheet workbook — 174
columns across 17 tables — and for that minute and a bit the process answered no HTTP request
at all. The browser gives up at 60 seconds.

Cutting the poll from 1,443,751 bytes to 24,775 was worth doing and could not have fixed this:
a small response still cannot be sent by a loop that is not running.
"""
from __future__ import annotations

import ast
import os

_NODE = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes", "udr_node.py")


def _source() -> str:
    return open(_NODE, encoding="utf-8").read()


def _udr_node_fn() -> ast.AsyncFunctionDef:
    tree = ast.parse(_source())
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "udr_node")
    return fn


class TestThePipelineIsNotCalledOnTheLoop:
    def test_it_is_awaited_through_to_thread(self):
        src = _source()
        assert "await asyncio.to_thread(" in src

    def test_it_is_never_called_directly(self):
        """`result = run_udr_pipeline(` is the line that held the loop for 69 seconds."""
        src = _source()
        assert "result = run_udr_pipeline(" not in src

    def test_the_only_call_to_the_pipeline_is_inside_to_thread(self):
        """Walk the tree rather than the text: a second direct call added later would not show
        up in a string search for the old line."""
        fn = _udr_node_fn()
        direct = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = getattr(f, "id", None) or getattr(f, "attr", None)
            if name != "run_udr_pipeline":
                continue
            direct.append(node)
        # Never called here at all: partial() takes it as a value and the thread calls it.
        assert direct == []
        partials = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Call)
                    and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "partial"]
        assert len(partials) == 1
        first_arg = partials[0].args[0]
        assert getattr(first_arg, "id", None) == "run_udr_pipeline"

    def test_the_imports_it_needs_are_at_the_top(self):
        src = _source()
        head = src[:src.index("logger = logging.getLogger")]
        assert "import asyncio" in head
        assert "from functools import partial" in head


class TestNothingElseChanged:
    def test_the_failure_guard_still_wraps_it(self):
        """A UDR error must still not fail the migration — the run is already written."""
        src = _source()
        assert "never fail the migration on a UDR error" in src

    def test_every_argument_still_travels(self):
        """partial() must carry the same keywords, or the analysis silently changes shape."""
        fn = _udr_node_fn()
        partial_call = next(n for n in ast.walk(fn)
                            if isinstance(n, ast.Call)
                            and (getattr(n.func, "id", None) or getattr(n.func, "attr", None)) == "partial")
        kw = {k.arg for k in partial_call.keywords}
        assert kw == {
            "run_id", "dest_table_by_source", "documents_ingested", "mapping_decisions",
            "confidence_by_source", "pk_override_by_table", "classification_decisions",
            "alias_resolver", "field_resolver",
        }
        # full_tables stays positional, as the pipeline declares it.
        assert len(partial_call.args) == 2
        assert getattr(partial_call.args[1], "id", None) == "full_tables"

    def test_it_still_only_runs_on_a_completed_write(self):
        src = _source()
        assert 'if state.get("status") != "complete" or not state.get("full_tables"):' in src
