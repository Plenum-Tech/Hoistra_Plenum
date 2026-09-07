"""F5-5 — explicit 95% deterministic cutoff (pure). Run: python tests/test_deterministic_cutoff.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graph.nodes.mapping_cutoff import (  # noqa: E402
    DETERMINISTIC_CUTOFF,
    is_auto_deterministic,
    partition_by_cutoff,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── the single constant ──────────────────────────────────────────────────────
check("cutoff is 0.95", DETERMINISTIC_CUTOFF == 0.95)

# ── is_auto_deterministic: >= cutoff (inclusive boundary) ────────────────────
check("0.97 auto", is_auto_deterministic(0.97) is True)
check("0.95 boundary auto (inclusive)", is_auto_deterministic(0.95) is True)
check("0.94 not auto", is_auto_deterministic(0.94) is False)
check("0.88 routes to semantic", is_auto_deterministic(0.88) is False)
check("None confidence not auto", is_auto_deterministic(None) is False)

# ── partition: >=95 kept, <95 source rerouted ───────────────────────────────
mappings = [
    {"source_field": "asset_tag", "target_field": "asset_code", "confidence": 0.99},  # keep
    {"source_field": "loc", "target_field": "location", "confidence": 0.88},          # reroute
    {"source_field": "stat", "target_field": "status", "confidence": 0.95},           # keep (boundary)
    {"source_field": "vendor", "target_field": "supplier", "confidence": 0.90},        # reroute
]
kept, reroute = partition_by_cutoff(mappings)
check("kept are the >=95 matches", {m["source_field"] for m in kept} == {"asset_tag", "stat"})
check("reroute are the <95 sources", set(reroute) == {"loc", "vendor"})

# ── a source kept under one target is NOT rerouted by a weaker duplicate ──────
dup = [
    {"source_field": "code", "target_field": "asset_code", "confidence": 0.99},  # keep
    {"source_field": "code", "target_field": "barcode", "confidence": 0.70},     # below, but source kept
]
kdup, rdup = partition_by_cutoff(dup)
check("kept includes the high-conf mapping", any(m["target_field"] == "asset_code" for m in kdup))
check("source kept under one target is not rerouted", rdup == [])

# ── two weak duplicates for the same source reroute it ONCE ──────────────────
weak = [
    {"source_field": "misc", "target_field": "a", "confidence": 0.60},
    {"source_field": "misc", "target_field": "b", "confidence": 0.50},
]
_, rweak = partition_by_cutoff(weak)
check("weak duplicate source rerouted once", rweak == ["misc"])

# ── missing / invalid confidence treated as 0 -> reroute ─────────────────────
bad = [{"source_field": "x", "target_field": "y"}, {"source_field": "z", "target_field": "w", "confidence": "n/a"}]
kbad, rbad = partition_by_cutoff(bad)
check("missing/invalid confidence -> rerouted, none kept", kbad == [] and set(rbad) == {"x", "z"})

print("\nALL TESTS PASSED")
