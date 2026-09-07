"""Content sanity-check that overrides an UNCERTAIN semantic table match.

'works' (asset columns: tagnum, product_name, make, condition) was being pulled to 'work_orders'
by name similarity even though its columns fit 'assets'. The pre-semantic deterministic mapper
now second-guesses a LOW-confidence semantic table match against real column overlap
(rank_target_tables): if the LLM's table barely fits but a different real table clearly fits, the
content wins. Confident name/levenshtein matches (≥0.95) are never second-guessed.

deterministic_mapper isn't importable offline (anthropic); this guards the two pieces the override
relies on — the content signal (rank_target_tables) and the decision rule. Run:
python tests/test_udr_table_content_override.py
"""

import importlib
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg
rank_target_tables = importlib.import_module("matchers.table_router").rank_target_tables

_fails = 0


def check(name, cond):
    global _fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        _fails += 1


def _content_override(cands, llm_target, conf):
    """Mirror of the deterministic_mapper rule: trust content over a weak semantic guess."""
    if not (cands and llm_target and conf < 0.95):
        return None
    top = cands[0]
    top_pct = float(top.get("pct") or 0.0)
    llm_pct = next((float(c.get("pct") or 0.0) for c in cands if c.get("table") == llm_target), 0.0)
    if top.get("table") and top["table"] != llm_target and top_pct >= 0.30 and llm_pct <= top_pct - 0.25:
        return top["table"]
    return None


# Real column sets from the stress-test workbook.
WORKS = ["tagnum", "id", "product_name", "make", "property_ref", "condition"]   # → assets
WORKORDERS = ["id", "asset_no", "description", "priority", "status"]            # → work_orders

_cw = rank_target_tables(WORKS)
_cwo = rank_target_tables(WORKORDERS)
print("works content:", [(r["table"], r["pct"]) for r in _cw])
print("WorkOrders content:", [(r["table"], r["pct"]) for r in _cwo])

check("content: 'works' columns rank 'assets' first",
      bool(_cw) and _cw[0]["table"] == "assets")
check("content: 'work_orders' has ~no overlap with 'works' columns (absent or weak)",
      not any(c["table"] == "work_orders" for c in _cw))

# The actual fix: an uncertain (0.78) semantic guess of work_orders is overridden to assets.
check("works: weak semantic work_orders@0.78 → overridden to assets",
      _content_override(_cw, "work_orders", 0.78) == "assets")
# A CONFIDENT match is never second-guessed.
check("works: a confident match (≥0.95) is NOT overridden",
      _content_override(_cw, "work_orders", 0.98) is None)
# A genuine work-order sheet keeps work_orders (strong content support + conf gate).
check("WorkOrders: work_orders@0.95 is NOT overridden (confident)",
      _content_override(_cwo, "work_orders", 0.95) is None)
check("WorkOrders: even a hypothetical weak guess isn't overridden (work_orders fits its columns)",
      _content_override(_cwo, "work_orders", 0.80) is None)

if _fails:
    print(f"\n{_fails} TEST(S) FAILED")
    sys.exit(1)
print("\nALL TESTS PASSED")
