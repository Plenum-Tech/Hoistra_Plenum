"""Creation-date bias in deterministic_mapper._build_candidate_fits.

A 'raised / opened / logged / reported / created' date is a CREATION timestamp and must prefer
created_at over the fuzzily-close-but-wrong updated_at (a MODIFICATION timestamp).

deterministic_mapper imports anthropic + relative packages, so it can't be imported offline; we
AST-extract the pure helpers + _build_candidate_fits and exec them in isolation (same technique the
team uses to test that module's pure functions). Run: python tests/test_udr_date_bias.py
"""

import ast
import difflib
import os
import re
import sys

_MOD = os.path.join(os.path.dirname(__file__), "..", "src", "graph", "nodes", "deterministic_mapper.py")
_src = open(_MOD, encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {"difflib": difflib, "re": re}
for _node in _tree.body:  # module-level constants the helpers close over
    if isinstance(_node, ast.Assign):
        try:
            exec(ast.get_source_segment(_src, _node), _ns)
        except Exception:
            pass
for _node in _tree.body:  # undecorated top-level functions (skip @timed_node etc.)
    if isinstance(_node, ast.FunctionDef) and not _node.decorator_list:
        try:
            exec(ast.get_source_segment(_src, _node), _ns)
        except Exception:
            pass

_bcf = _ns["_build_candidate_fits"]
_nf = _ns["_normalize_field_name"]

_fails = 0


def check(name, cond):
    global _fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails += 1


def top_fit(source_field, cols, samples=None):
    fits = _bcf(source_field, None, 0.0, {_nf(c): c for c in cols}, {},
                samples=samples or ["2026-01-04", "2026-02-11", "2026-03-02"])
    return (fits[0]["target_field"] if fits else None), fits


print("[date-bias] creation-style dates prefer created_at")
for sf in ("date_raised", "raised_date", "date_opened", "opened_date", "date_logged",
           "date_reported", "date_received", "date_entered"):
    t, _ = top_fit(sf, ["updated_at", "created_at", "site_id"])
    check(f"{sf} → created_at (not updated_at)", t == "created_at")

print("\n[date-bias] modification / neutral dates are NOT biased")
check("last_updated → updated_at (modification date untouched)",
      top_fit("last_updated", ["updated_at", "created_at"])[0] == "updated_at")
check("date_modified → updated_at (modification date untouched)",
      top_fit("date_modified", ["updated_at", "created_at"])[0] == "updated_at")
check("start_date → not forced to created_at ('start' is not a creation token)",
      top_fit("start_date", ["updated_at", "created_at", "scheduled_date"])[0] != "created_at")

print("\n[date-bias] updated_at is demoted below the cutoff for a creation date")
_t, _fits = top_fit("date_raised", ["updated_at", "created_at"])
check("date_raised candidate list does not surface updated_at as the pick",
      _t == "created_at")

if _fails:
    print(f"\n{_fails} TEST(S) FAILED")
    sys.exit(1)
print("\nALL TESTS PASSED")
