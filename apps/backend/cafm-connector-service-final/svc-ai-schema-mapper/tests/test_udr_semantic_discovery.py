"""Feature 7 Job 1 — semantic relationship discovery foundation (pure).
Run: python tests/test_udr_semantic_discovery.py"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.semantic_discovery import (  # noqa: E402
    LLM_INFERRED,
    discover_relationships,
    normalize_discovered_edge,
    normalize_discovered_edges,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── normalize one proposed connection -> llm_inferred edge ───────────────────
e = normalize_discovered_edge(
    {"src": "work_orders", "dst": "work_orders", "relationship": "RECURRING_DEFECT",
     "confidence": 0.72, "rationale": "Same north-wall damp described 3 months apart"}
)
check("provenance is llm_inferred", e["provenance"] == LLM_INFERRED == "llm_inferred")
check("edge carries src/dst/rel", e["src_entity"] == "work_orders" and e["dst_entity"] == "work_orders"
      and e["rel_type"] == "RECURRING_DEFECT")
check("confidence clamped float", e["confidence"] == 0.72)
check("evidence from rationale", "north-wall damp" in e["evidence"])

# alternate key names + clamping + default rel
e2 = normalize_discovered_edge({"src_entity": "assets", "dst_entity": "sites", "confidence": 5})
check("alt keys + default rel + clamp", e2["rel_type"] == "RELATED" and e2["confidence"] == 1.0)
check("missing entity -> None", normalize_discovered_edge({"src": "a"}) is None)

# ── batch normalize drops malformed ──────────────────────────────────────────
batch = normalize_discovered_edges([
    {"src": "a", "dst": "b"},
    {"src": "a"},            # dropped (no dst)
    "not a dict",            # dropped
    {"from": "c", "to": "d", "confidence": "n/a"},  # confidence default 0.5
])
check("batch keeps 2 valid edges", len(batch) == 2)
check("invalid confidence -> 0.5 default", batch[1]["confidence"] == 0.5)
check("all batch edges are llm_inferred", all(x["provenance"] == "llm_inferred" for x in batch))

# ── discover is async + no-op when no LLM hook (foundation present) ──────────
check("discover_relationships is async", inspect.iscoroutinefunction(discover_relationships))
res = asyncio.run(discover_relationships([{"entity": "asset", "text": "..."}], llm=None, run_id="r"))
check("no LLM hook -> no-op (no DB touched)", res == {"discovered": 0, "edges": []})

# with an llm hook that proposes nothing useful -> still no edges, no DB
res2 = asyncio.run(discover_relationships(["c1"], llm=lambda c: [{"src": "x"}], run_id="r"))
check("llm proposes only malformed -> 0 discovered (no DB)", res2 == {"discovered": 0, "edges": []})

print("\nALL TESTS PASSED")
