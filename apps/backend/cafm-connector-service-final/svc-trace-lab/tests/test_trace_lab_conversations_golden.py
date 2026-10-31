"""Golden conversations in trace_lab.conversations without a database: a golden run id is a run filter,
golden keys and turn ids are recognised, and the recorded provenance never carries the expected answer
(G0 addendum 2.5, Phase 3 Task 13)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from trace_lab import capture, conversations as cv

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
RUN = "1A2B3C4D-5E6F-4000-8000-000000000000"


def test_a_golden_run_id_is_a_run_filter():
    f = cv.make_filter(now=NOW, run=RUN)
    assert f.run == RUN.lower() and not isinstance(f, cv.ModelCallFilter)
    assert cv.make_filter(now=NOW).run == "live"
    with pytest.raises(cv.BadRequest) as e:
        cv.make_filter(now=NOW, run=RUN, calls=True)
    assert e.value.reason == "golden_calls_unavailable"


def test_golden_keys_and_turns_are_recognised():
    assert cv.GOLDEN_TURN_PREFIX == capture.TURN_PREFIX
    assert cv.is_golden_turn(capture.new_turn_id()) and not cv.is_golden_turn("turn-0123456789abcdef")
    assert cv.is_golden_key("s:golden-1a2b3c4d5e6f-21-r1")
    assert not cv.is_golden_key("s:3f1c2d3e-live-session") and not cv.is_golden_key("t:golden-x")


def test_the_recorded_provenance_names_the_case_and_never_its_answer():
    turn = {"golden": {"page_context": "The user is on Compliance.", "asker_role": "admin",
                       "expected_answer": "SECRET"}}
    row = {"run_id": uuid.UUID(RUN), "case_key": "21", "rep": 2, "msg_idx": 1, "question": "And reactive?"}
    meta = cv.golden_meta(turn, row)
    assert (meta["channel"], meta["channel_source"], meta["golden_case_key"], meta["rep"]) == (
        "golden", "recorded", "21", 2)
    assert meta["asker_question"] == "And reactive?" and meta["context_text"] == "The user is on Compliance."
    assert "SECRET" not in repr(meta) and meta["golden_run_id"] == RUN.lower()
