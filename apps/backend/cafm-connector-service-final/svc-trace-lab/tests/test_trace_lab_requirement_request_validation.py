"""A requirement request is checked before anything is written, and each refusal says what is actually wrong
(no database: the checks run first in create, revise and withdraw)."""
from __future__ import annotations

import pytest

from trace_lab import requirements
from trace_lab.review_errors import Invalid

STATEMENT = "Every answer cites its source rows."
DOC = "apps/backend/cafm-connector-service-final/svc-deepagents/skills/query-builder/fm-lens.md"


def _validate(**over):
    kw = dict(area="GEN", level="technical", statement=STATEMENT, implementations=[])
    kw.update(over)
    return requirements._validate(**kw)


def _refused(**over) -> Invalid:
    with pytest.raises(Invalid) as e:
        _validate(**over)
    assert e.value.status == 422 and e.value.error
    return e.value


def test_a_note_that_is_missing_is_told_to_say_what_changed_and_one_too_long_to_be_shorter():
    missing = _refused(note="  ", note_required=True)
    assert missing.reason == "note_required" and "what changed" in missing.error
    long = _refused(note="x" * (requirements.NOTE_MAX + 1), note_required=True)
    assert long.reason == "note_too_long" and str(requirements.NOTE_MAX) in long.error
    assert "what changed" not in long.error
    assert _validate(note="x" * requirements.NOTE_MAX, note_required=True) == []


def test_a_quote_that_is_missing_is_told_to_quote_and_one_too_long_to_be_shorter():
    blank = _refused(implementations=[{"doc": DOC, "quote": "  "}])
    assert blank.reason == "quote_required" and "Quote the sentence" in blank.error
    long = _refused(implementations=[{"doc": DOC, "quote": "q" * (requirements.QUOTE_MAX + 1)}])
    assert long.reason == "quote_too_long" and str(requirements.QUOTE_MAX) in long.error
    assert _validate(implementations=[{"doc": DOC, "quote": "q" * requirements.QUOTE_MAX}])[0]["quote"]


async def test_withdraw_refuses_a_missing_or_too_long_note_before_touching_any_database():
    too_long = "x" * (requirements.NOTE_MAX + 1)
    for note, reason in (("", "note_required"), ("   ", "note_required"), (too_long, "note_too_long")):
        with pytest.raises(Invalid) as e:
            await requirements.withdraw(None, "GEN-1", note=note)
        assert e.value.reason == reason, note[:5]


async def test_revise_refuses_a_too_long_note_before_touching_any_database():
    with pytest.raises(Invalid) as e:
        await requirements.revise(None, "GEN-1", statement=STATEMENT, implementations=[], motivating_turn_ids=[],
                                  note="x" * (requirements.NOTE_MAX + 1))
    assert e.value.reason == "note_too_long"
