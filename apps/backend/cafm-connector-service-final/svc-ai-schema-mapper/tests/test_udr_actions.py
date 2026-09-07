"""Tests for Feature 4 Section 3 inline actions (AL.3/AL.4). Pure (no DB).
Run: python tests/test_udr_actions.py"""
import inspect
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import actions as A  # noqa: E402
from udr import actions_persist as AP  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── options: normalized + capped at 4 (AL.3 AC3) ─────────────────────────────
norm = A.normalize_options(["a", "b", "c", "d", "e", "f"])
check("options capped at 4", len(norm) == A.MAX_OPTIONS == 4)
check("plain option -> {id,label,value}", norm[0] == {"id": "0", "label": "a", "value": "a"})
dict_norm = A.normalize_options([{"id": "x", "label": "Keep 48 hrs", "value": 48}])
check("dict option preserved", dict_norm[0] == {"id": "x", "label": "Keep 48 hrs", "value": 48})

# ── build_inline_action: defaults + kind guard ───────────────────────────────
act = A.build_inline_action("change asset 4471 -> 4465", kind="field_change",
                            options=[{"id": "4465", "label": "4465"}, {"id": "4471", "label": "keep 4471"}])
check("action label + kind", act["label"].startswith("change asset") and act["kind"] == "field_change")
check("action pending + 2 options", act["status"] == "pending" and len(act["options"]) == 2)
default_act = A.build_inline_action("approve booking?")
check("default approve/reject options", [o["id"] for o in default_act["options"]] == ["approve", "reject"])
bad_kind = A.build_inline_action("x", kind="nonsense")
check("invalid kind falls back to approval", bad_kind["kind"] == "approval")

# ── 15-min deadline + overdue predicate (AL.4 AC1) ───────────────────────────
created = datetime(2026, 6, 16, 12, 0, 0)
check("deadline = created + 15 min", A.deadline_from(created) == created + timedelta(minutes=15))
check("not overdue before deadline", A.is_overdue(A.deadline_from(created), created + timedelta(minutes=14)) is False)
check("overdue at/after deadline", A.is_overdue(A.deadline_from(created), created + timedelta(minutes=15)) is True)

# ── select_overdue: only pending + past deadline ─────────────────────────────
now = datetime(2026, 6, 16, 12, 20, 0)
acts = [
    {"id": "a1", "status": "pending", "deadline_at": datetime(2026, 6, 16, 12, 15, 0)},   # overdue
    {"id": "a2", "status": "pending", "deadline_at": datetime(2026, 6, 16, 12, 25, 0)},   # not yet
    {"id": "a3", "status": "resolved", "deadline_at": datetime(2026, 6, 16, 12, 10, 0)},  # done
]
overdue = A.select_overdue(acts, now)
check("only the overdue pending action selected", [a["id"] for a in overdue] == ["a1"])

# ── option validity guard (never resolve with an un-offered option) ──────────
check("valid option accepted", A.is_valid_option(act, "4465") is True)
check("invalid option rejected", A.is_valid_option(act, "9999") is False)

# ── Pending Human Response entry: red, no autonomous decision (AL.4) ─────────
pend = A.build_pending_response_entry({"id": "a1", "entry_id": "e1", "label": "change asset 4471 -> 4465"})
check("pending entry is red + pending_human_input",
      pend["notif_color"] == "red" and pend["status"] == "pending_human_input")
check("pending entry has NO chosen option (no autonomous proceed)", "chosen_option_id" not in pend)
check("pending entry refs the action + entry", pend["refs"] == {"action_id": "a1", "entry_id": "e1"})

# ── persistence: pure column mapper + async surface ──────────────────────────
cols = AP.action_to_columns(act, entry_id="e1", organization_id="org-1", created_at=created)
check("columns carry entry + status + label",
      cols["entry_id"] == "e1" and cols["status"] == "pending" and cols["label"].startswith("change asset"))
check("columns stamp 15-min deadline", cols["deadline_at"] == created + timedelta(minutes=15))
check("columns normalize options (<=4)", len(cols["options"]) == 2)

for fn in ("record_action", "list_actions", "list_pending_actions", "resolve_action", "sweep_timeouts"):
    check(f"{fn} is async", inspect.iscoroutinefunction(getattr(AP, fn)))

print("\nALL TESTS PASSED")
