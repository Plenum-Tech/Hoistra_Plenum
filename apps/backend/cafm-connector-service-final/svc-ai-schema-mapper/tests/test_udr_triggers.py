"""Tests for Feature 4 trigger emitters (AL.1 triggers 2-4, pure).
Run: python tests/test_udr_triggers.py"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr import triggers as T  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── trigger 2: external input ────────────────────────────────────────────────
ext = T.build_external_input_entry(source="email", summary="New work-order request from Apex Lifts",
                                   refs={"message_id": "m1"})
check("external_input trigger + green", ext["trigger"] == "external_input" and ext["notif_color"] == "green")
check("external_input carries source + extra refs",
      ext["refs"]["source"] == "email" and ext["refs"]["message_id"] == "m1")
check("external_input default detail", "Received from email" in ext["trigger_detail"])

# ── trigger 3: threshold ─────────────────────────────────────────────────────
thr = T.build_threshold_entry(summary="SLA breach on WO-22", status="escalated")
check("threshold trigger + orange", thr["trigger"] == "threshold" and thr["notif_color"] == "orange")

# ── trigger 4: scheduled ─────────────────────────────────────────────────────
sch = T.build_scheduled_entry(summary="Quarterly chiller PM")
check("scheduled trigger + green", sch["trigger"] == "scheduled" and sch["notif_color"] == "green")

# ── expiry scan (trigger 3) ──────────────────────────────────────────────────
now = datetime(2026, 6, 16, 9, 0, 0)
items = [
    {"id": "c1", "name": "Lift inspection cert", "expires_at": "2026-07-04"},   # 18 days -> soon
    {"id": "c2", "name": "Fire cert", "expires_at": "2026-06-10"},              # 6 days ago -> overdue
    {"id": "c3", "name": "AC warranty", "expires_at": "2027-01-01"},            # >30 days -> ignore
    {"id": "c4", "name": "No date"},                                            # no expiry -> ignore
]
entries = T.scan_expiries(items, now=now, within_days=30)
check("expiry scan emits 2 (soon + overdue)", len(entries) == 2)
by_item = {e["refs"]["item_id"]: e for e in entries}
check("expiring-soon -> escalated/orange + days_remaining 18",
      by_item["c1"]["status"] == "escalated" and by_item["c1"]["notif_color"] == "orange"
      and by_item["c1"]["refs"]["days_remaining"] == 18)
check("overdue -> pending/red + overdue_days 6",
      by_item["c2"]["status"] == "pending_human_input" and by_item["c2"]["notif_color"] == "red"
      and by_item["c2"]["refs"]["overdue_days"] == 6)
check("all expiry entries are threshold trigger", all(e["trigger"] == "threshold" for e in entries))

# tighter window excludes the 18-day one
narrow = T.scan_expiries(items, now=now, within_days=7)
check("7-day window -> only the overdue one", {e["refs"]["item_id"] for e in narrow} == {"c2"})

# ── schedule scan (trigger 4) ────────────────────────────────────────────────
sched_items = [
    {"id": "s1", "title": "Generator load test", "due_at": "2026-06-16T08:00:00"},  # due (past)
    {"id": "s2", "title": "Roof inspection", "due_at": "2026-06-20T08:00:00"},       # future
    {"id": "s3", "title": "no due"},                                                  # ignore
]
due = T.scan_due_schedules(sched_items, now=now)
check("schedule scan emits only the due item", {e["refs"]["item_id"] for e in due} == {"s1"})
check("scheduled entry trigger + green", due[0]["trigger"] == "scheduled" and due[0]["notif_color"] == "green")

print("\nALL TESTS PASSED")
