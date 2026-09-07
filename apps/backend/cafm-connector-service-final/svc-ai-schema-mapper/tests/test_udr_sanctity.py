"""Tests for Feature 7 F7-5 sanctity check (pure). Run: python tests/test_udr_sanctity.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import sanctity as S  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── level extraction ─────────────────────────────────────────────────────────
check("level 'Level 5' -> 5", S.extract_level("Repair on Level 5 plant room") == 5)
check("level 'L3' -> 3", S.extract_level("Filter on L3 chiller") == 3)
check("level '3rd floor' -> 3", S.extract_level("works on 3rd floor") == 3)
check("level 'ground floor' -> 0", S.extract_level("ground floor lobby") == 0)
check("level 'basement' -> -1", S.extract_level("basement pump") == -1)
check("level none when absent", S.extract_level("general maintenance") is None)

# ── asset-type extraction ────────────────────────────────────────────────────
check("'AHU' -> ahu", S.extract_asset_types("service the AHU") == {"ahu"})
check("'air handling unit' -> ahu", "ahu" in S.extract_asset_types("air handling unit fault"))
check("'chiller' + 'pump' both", S.extract_asset_types("pump on the chiller") == {"pump", "chiller"})
check("no type -> empty", S.extract_asset_types("routine check") == set())

# ── implausible: level AND type mismatch (the canonical example) ─────────────
v = S.validate_relationship("Replaced pump on Level 5 AHU", {"name": "Level 3 Chiller", "loc": "L3"})
check("level+type mismatch -> implausible", v["plausible"] is False)
check("level+type mismatch -> 0.95 confidence", v["confidence"] == 0.95)
check("mismatch reason mentions misallocation", "misallocation" in v["reason"].lower())
signals = {m["signal"] for m in v["mismatches"]}
check("both level and asset_type flagged", signals == {"level", "asset_type"})

# ── level-only mismatch -> 0.90 ──────────────────────────────────────────────
vl = S.validate_relationship("Inspection on level 5", {"location": "level 2 area"})
check("level-only -> implausible 0.90", vl["plausible"] is False and vl["confidence"] == 0.9)
check("level-only single mismatch", {m["signal"] for m in vl["mismatches"]} == {"level"})

# ── type-only mismatch -> 0.85 ───────────────────────────────────────────────
vt = S.validate_relationship("Chiller annual service", {"desc": "pump station"})
check("type-only -> implausible 0.85", vt["plausible"] is False and vt["confidence"] == 0.85)

# ── plausible: agreeing level + type ─────────────────────────────────────────
vp = S.validate_relationship("Replace filter on L3 chiller", {"desc": "Chiller - Level 3"})
check("agreeing link -> plausible", vp["plausible"] is True)
check("agreeing link -> high confidence (2 agreements)", vp["confidence"] == 0.9)
check("agreeing link -> no mismatches", vp["mismatches"] == [])

# ── insufficient evidence -> plausible, low confidence ───────────────────────
vi = S.validate_relationship("general maintenance required", {"desc": "equipment"})
check("insufficient evidence -> plausible 0.3", vi["plausible"] is True and vi["confidence"] == 0.3)
check("insufficient evidence reason", "insufficient" in vi["reason"].lower())

# ── F7-6: flagged verdict -> escalatable entry + inline action ───────────────
built = S.build_sanctity_activity(v, wo_id="W123", asset_id="A4471")
entry, action = built["entry"], built["action"]
check("sanctity entry is escalated/orange", entry["status"] == "escalated" and entry["notif_color"] == "orange")
check("sanctity entry trigger=threshold", entry["trigger"] == "threshold")
check("sanctity entry refs wo+asset+verdict",
      entry["refs"]["wo_id"] == "W123" and entry["refs"]["asset_id"] == "A4471"
      and entry["refs"]["sanctity"]["confidence"] == 0.95)
check("sanctity action has 3 options (reassignment)",
      action["kind"] == "reassignment" and len(action["options"]) == 3
      and {o["id"] for o in action["options"]} == {"confirm", "reassign", "coincidental"})

print("\nALL TESTS PASSED")
