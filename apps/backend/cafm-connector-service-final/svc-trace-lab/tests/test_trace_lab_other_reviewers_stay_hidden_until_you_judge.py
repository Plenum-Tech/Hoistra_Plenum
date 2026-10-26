"""Blind review: other reviewers' verdicts, notes and comments stay hidden from the caller until the caller
has saved a verdict on that answer, the answer's batch item is complete, or the caller revealed it; the
platform owner sees everything (spec "Open coding ..." 2.3, 2.5). Pure rules here; the database path is in
the _sql twin of this file."""
from __future__ import annotations

from trace_lab import annotations

ME, A, B = "me", "a", "b"


def head(tid, reviewer, verdict="wrong", tombstone=False):
    return {"turn_id": tid, "reviewer_id": reviewer, "verdict": verdict, "tombstone": tombstone}


def test_nothing_is_visible_before_you_judge():
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=[head("t1", A)], items={}, revealed=set()) == set()


def test_your_own_live_verdict_opens_the_answer_and_a_tombstone_does_not():
    heads = [head("t1", A), head("t1", ME, "right"), head("t2", A), head("t2", ME, None, tombstone=True)]
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items={}, revealed=set()) == {"t1"}


def test_a_complete_batch_item_has_nothing_left_to_keep_blind():
    heads = [head("t1", A), head("t2", A)]
    items = {"t1": {"reviews_required": 1}, "t2": {"reviews_required": 2}}
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items=items, revealed=set()) == {"t1"}
    heads.append(head("t2", B, "right"))
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=heads, items=items, revealed=set()) == {"t1", "t2"}


def test_a_tombstoned_verdict_does_not_count_towards_a_complete_item():
    heads = [head("t1", A, None, tombstone=True)]
    items = {"t1": {"reviews_required": 1}}
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=heads, items=items, revealed=set()) == set()


def test_the_same_reviewer_counts_once_towards_a_complete_item():
    heads = [head("t1", A), head("t1", A, "right")]
    items = {"t1": {"reviews_required": 2}}
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=heads, items=items, revealed=set()) == set()


def test_a_reveal_opens_the_answer():
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=[head("t1", A)], items={}, revealed={"t1"}) == {"t1"}
    assert annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1", "t2"], heads=[], items={}, revealed={"t2"}) == {"t2"}


def test_the_platform_owner_sees_everything():
    assert annotations.visible_set(me=ME, is_owner=True, turn_ids=["t1", "t2"], heads=[], items={}, revealed=set()) == {"t1", "t2"}


def test_a_turn_nobody_judged_stays_closed_for_a_non_owner_and_the_result_only_names_asked_turns():
    out = annotations.visible_set(me=ME, is_owner=False, turn_ids=["t1"], heads=[head("t9", ME, "right")], items={}, revealed={"t7"})
    assert out == set()
