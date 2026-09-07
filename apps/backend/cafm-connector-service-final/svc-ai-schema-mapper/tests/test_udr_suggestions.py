"""Tests for Feature 4 AL.5 refinements/follow-ups (pure). Run: python tests/test_udr_suggestions.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import suggestions as S  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── caps: <=3 refinements, <=2 follow-ups (AL.5 AC2) ─────────────────────────
out = S.build_suggestions(
    refinements=["a", "b", "c", "d", "e"],
    follow_ups=["x", "y", "z"],
)
check("refinements capped at 3", len(out["refinements"]) == S.MAX_REFINEMENTS == 3)
check("follow_ups capped at 2", len(out["follow_ups"]) == S.MAX_FOLLOW_UPS == 2)

# ── normalization: id/kind/label/prompt/requires_approval ────────────────────
r0 = out["refinements"][0]
check("refinement shape", set(r0.keys()) == {"id", "kind", "label", "prompt", "requires_approval"})
check("refinement kind + no-approval default", r0["kind"] == "refinement" and r0["requires_approval"] is False)
check("follow_up requires approval by default", out["follow_ups"][0]["requires_approval"] is True)
check("plain string -> label == prompt", r0["label"] == "a" and r0["prompt"] == "a")

dictform = S.build_suggestions(
    refinements=[{"id": "rf1", "label": "Enrich assets", "prompt": "Enrich the asset records"}],
)
check("dict refinement preserves id/prompt",
      dictform["refinements"][0]["id"] == "rf1" and dictform["refinements"][0]["prompt"] == "Enrich the asset records")

# ── udr_suggestions: derives meaningful suggestions from a run ───────────────
blocked = S.udr_suggestions(table_count=20, relationship_count=12, test1_flags=2, test2_flags=3,
                            blocked=True, sample_table="assets")
labels = [s["label"] for s in blocked["refinements"]]
check("work-cloud refinement present", any("work cloud for assets" in s for s in labels))
check("test2 overlap refinement present (plural)", any("3 unexplained column overlaps" in s for s in labels))
check("refinements still capped at 3", len(blocked["refinements"]) == 3)
fu_labels = [s["label"] for s in blocked["follow_ups"]]
check("blocked -> escalation follow-up", any("Escalate the blocked UDR" in s for s in fu_labels))
check("test1 flags -> approve-remediation follow-up", any("Approve remediation for 2 Test 1 items" in s for s in fu_labels))
check("follow_ups all require approval", all(s["requires_approval"] for s in blocked["follow_ups"]))

# clean run: no flags, not blocked -> no follow-ups, only metadata refinement
clean = S.udr_suggestions(table_count=5, relationship_count=0, test1_flags=0, test2_flags=0, blocked=False)
check("clean run -> no follow-ups", clean["follow_ups"] == [])
check("clean run -> a metadata refinement", any("metadata" in s["label"] for s in clean["refinements"]))

# empty run -> nothing (AL.5 AC4: not generated for every query)
empty = S.udr_suggestions(table_count=0, relationship_count=0)
check("empty run -> no suggestions", empty["refinements"] == [] and empty["follow_ups"] == [])

print("\nALL TESTS PASSED")
