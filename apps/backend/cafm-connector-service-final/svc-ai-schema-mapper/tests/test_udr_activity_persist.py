"""Tests for activity-log persistence (import-clean + pure helper). Run: python tests/test_udr_activity_persist.py"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import activity_persist as ap  # noqa: E402
from udr.activity import build_udr_activity_entry  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── module imports without DB config (lazy imports) ──────────────────────────
check("module imports clean (no DB needed)", ap is not None)

# ── async DB functions are coroutines ────────────────────────────────────────
for fn in ("record_activity", "list_activity", "mark_activity_read", "unread_summary"):
    check(f"{fn} is async", inspect.iscoroutinefunction(getattr(ap, fn)))

# ── pure entry_to_columns maps a built entry to row columns ──────────────────
entry = build_udr_activity_entry("run1", documents_ingested=3, table_count=20, column_count=101,
                                 script_ref="UDR-7")
cols = ap.entry_to_columns(entry, organization_id="org-x", session_id="sess-1")
check("columns trigger/status/notif", cols["trigger"] == "query" and cols["status"] == "completed"
      and cols["notif_color"] == "green")
check("columns outcome carried", "20 tables, 101 columns" in cols["outcome"])
check("columns refs + processing_log carried",
      cols["refs"]["udr_run_id"] == "run1" and "stages" in cols["processing_log"])
check("columns org + session", cols["organization_id"] == "org-x" and cols["session_id"] == "sess-1")

# defaults when fields missing
bare = ap.entry_to_columns({})
check("defaults applied", bare["trigger"] == "query" and bare["status"] == "completed"
      and bare["notif_color"] == "green" and bare["outcome"] == "")

# ── notif priority ordering ──────────────────────────────────────────────────
check("notif priority red>orange>green",
      ap.NOTIF_PRIORITY["red"] > ap.NOTIF_PRIORITY["orange"] > ap.NOTIF_PRIORITY["green"])

# ── cross-tenant isolation guard (no unscoped global scan) ───────────────────
# These short-circuit BEFORE any DB import, so they run without a database.
check("unread_summary without org returns empty (no cross-tenant scan)",
      asyncio.run(ap.unread_summary(organization_id=None)) == {"count": 0, "worst_color": None})
check("list_activity with no scope returns empty",
      asyncio.run(ap.list_activity(organization_id=None, session_id=None)) == [])

print("\nALL TESTS PASSED")
