"""Conversation grouping: session kinds, keys, labels, inferred attempts, turn headers with read-time
provenance, and the read-time question repair."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trace_lab import normalize as n
from trace_lab import provenance

T0 = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
FIRST = {"attempt": 1, "attempt_source": None, "retry_of": None}


def row(tid, sid="s1", q="How many boilers?", minutes=0, **kw):
    r = {"turn_id": tid, "session_id": sid, "question": q, "answer": "Four.", "route": None,
         "started_at": T0 + timedelta(minutes=minutes), "ok": True, "models": ["claude-sonnet-5"], "tool_calls": 1,
         "cost_usd": 0.01, "latency_ms": 900}
    r.update(kw)
    return r


def test_conversations_prefer_the_real_session_and_flag_inferred_groups():
    assert n.session_kind("3b1f-thread", thread_exists=True) == "observed"
    assert n.session_kind("3b1f-thread", thread_exists=False) == "inferred"
    assert n.session_kind(None, thread_exists=False) == "inferred"
    assert n.session_kind("   ", thread_exists=True) == "inferred"
    assert n.session_kind("\t", thread_exists=False) == "inferred"          # btrim() trims spaces only
    assert n.group_key("3b1f-thread", "turn-1") == "s:3b1f-thread"
    assert n.group_key(None, "turn-1") == "t:turn-1" and n.group_key("  ", "turn-2") == "t:turn-2"
    assert n.group_key("\t", "turn-3") == "s:\t"
    assert n.session_label("observed", "3b1f", title="Boiler certificates", golden=None) == "Boiler certificates"
    assert n.session_label("observed", "3b1f", title="x" * 100, golden=None) == "x" * 80
    assert n.session_label("observed", "3b1f", title=None, golden=None) == "Untitled conversation"
    assert n.session_label("inferred", "3b1f", title="Boiler certificates", golden=None) == "Session not in chat history"
    assert n.session_label("inferred", None, title=None, golden=None) == "Single turn, no session recorded"


def test_synthetic_sessions_are_labelled_and_never_observed():
    cases = {"golden-run1-case-2-rep1": "Golden run · B-07 · rep 2", "report-1a2b-1759300000": "Report card run",
             "docs-9": "Document upload", "ing-4": "Document upload", "skilllab-x": "Skill lab replay",
             "rerun": "Correction re-run", "rerun-abc": "Correction re-run"}
    for sid, label in cases.items():
        assert n.session_kind(sid, thread_exists=True) == "synthetic", sid
        assert n.session_label("synthetic", sid, title="ignored", golden={"case_key": "B-07", "rep": 2}) == label, sid
    assert n.session_label("synthetic", "golden-x", title=None, golden=None) == "Golden run · — · rep —"


def test_the_shared_rerun_session_is_never_one_conversation():
    assert n.group_key("rerun", "turn-a") == "r:turn-a"
    assert n.group_key("rerun", "turn-b") == "r:turn-b"
    assert n.group_key("rerun-123", "turn-c") == "r:turn-c"


def test_attempts_are_inferred_from_the_activity_row_then_the_same_question():
    turns = [row("t1", minutes=0), row("t2", q="Re-run with corrections (1): How many boilers?", minutes=1),
             row("t3", q="How   many BOILERS?", minutes=2), row("t4", q="Something else", minutes=3),
             row("t5", q="Different words", minutes=4)]
    acts = {"t5": {"payload": {"message": "Different words", "mode": "rerun", "rerun_of": "t4"}}}
    out = n.attempts(list(reversed(turns)), None, acts)
    assert out["t1"] == FIRST
    assert out["t2"] == {"attempt": 2, "attempt_source": "inferred", "retry_of": "t1"}
    assert out["t3"] == {"attempt": 3, "attempt_source": "inferred", "retry_of": "t2"}   # the latest same question
    assert out["t4"] == FIRST
    assert out["t5"] == {"attempt": 2, "attempt_source": "inferred", "retry_of": "t4"}


def test_a_retry_of_a_turn_outside_the_conversation_is_attempt_two():
    out = n.attempts([row("t9")], {}, {"t9": {"payload": {"rerun_of": "turn-elsewhere"}}})
    assert out["t9"] == {"attempt": 2, "attempt_source": "inferred", "retry_of": "turn-elsewhere"}
    assert n.attempts([]) == {}


def test_turn_headers_are_short_redacted_and_carry_inferred_provenance():
    wrapped = "ctx " * 50 + n.QUESTION_MARKER + "Which certificates expire? Bearer abcdefghijklmnop"
    h = n.turn_header(row("t1", q=wrapped, answer="Two. " * 100), None, turn_index=1, attempt=FIRST)
    assert h["question"] == "Which certificates expire? Bearer [redacted]"
    assert len(h["answer"]) <= 300 and h["channel"] == "chat" and h["channel_source"] == "inferred"
    assert h["origin"] is None and h["flags"] == [] and h["turn_index"] == 1 and h["tool_count"] == 1
    assert h["attempt"] == 1 and h["attempt_source"] is None
    cell = "Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?"
    h2 = n.turn_header(row("t2", q=cell, ok=False, answer="gate_interrupt"), None, turn_index=2, attempt=FIRST)
    assert (h2["channel"], h2["channel_source"], h2["origin"]) == ("ui_action", "inferred", "ui.matrix_cell:risk")
    assert h2["flags"] == ["failed", "workflow_stop"] and h2["ok"] is False


def test_a_header_reads_the_activity_payload_and_recorded_meta_when_given():
    support = {"payload": {"message": "how do I ingest?", "extra_context": provenance.SUPPORT_SESSION_MARK + "."}}
    h = n.turn_header(row("t1", q="how do I ingest?"), None, turn_index=1, attempt=FIRST, activity_input=support)
    assert (h["channel"], h["channel_source"]) == ("support", "inferred")
    meta = {"channel": "golden", "channel_source": "recorded", "origin": "golden.case"}
    h2 = n.turn_header(row("t2"), meta, turn_index=2, attempt=FIRST)
    assert (h2["channel"], h2["channel_source"], h2["origin"]) == ("golden", "recorded", "golden.case")


def test_the_question_is_repaired_from_the_strongest_evidence():
    assert n.repair_question({"question": "wrapped"}, {"asker_question": "Own words"}, None) == ("Own words", [])
    assert n.repair_question({"question": "wrapped"}, None, {"payload": {"message": "Typed"}}) == ("Typed", ["question_repaired"])
    assert n.repair_question({"question": "Typed"}, None, {"payload": {"message": "Typed"}}) == ("Typed", [])
    marked = "ctx" + n.QUESTION_MARKER + "a" + n.QUESTION_MARKER + "b"
    assert n.repair_question({"question": marked}, None, None) == ("b", ["question_repaired"])
    assert n.repair_question({"question": "x" * 4000}, None, None) == ("x" * 4000, ["question_wrapped"])
    assert n.repair_question({"question": "x" * 4000}, {"channel": "chat"}, None) == ("x" * 4000, [])
    assert n.repair_question({"question": "plain"}, None, None) == ("plain", [])
    assert n.repair_question({"question": None}, None, {"payload": "not a mapping"}) == ("", [])
