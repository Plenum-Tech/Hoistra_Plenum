"""Classifying the same document the same way twice.

One certificate PDF, three consecutive uploads through the deployed service, three different
answers: CG_2382, FIRE_ALARM_SERVICE, HSE_POLICY. The field it disagreed with itself about
decides which regulation pack the certificate is scored against and when it falls due.

Two causes: no temperature was passed, so the call ran at the SDK default of 1.0 on a task
whose whole job is picking one item from a supplied list; and it was a single sample, where
this service's architecture asks for three passes on compliance documents specifically.

The passes are stubbed here — this tests the counting, not the model.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.engines.compliance import classify


def vote(answers: list[Any]) -> Any:
    """Run the vote with the passes replaced by a fixed sequence of answers."""
    calls = {"n": 0}

    async def fake_once(**_kw: Any) -> Any:
        i = calls["n"]
        calls["n"] += 1
        got = answers[i] if i < len(answers) else None
        if isinstance(got, Exception):
            raise got
        return got

    original = classify._claude_classify_once
    classify._claude_classify_once = fake_once
    try:
        return asyncio.run(
            classify._claude_classify(
                packs=[], source_text="", file_name="cert.pdf", pdf_base64=None
            )
        ), calls["n"]
    finally:
        classify._claude_classify_once = original


def test_three_passes_are_taken():
    _, n = vote(["EICR", "EICR", "EICR"])
    assert n == classify._CLASSIFY_PASSES == 3


def test_unanimous_is_the_answer():
    assert vote(["EICR", "EICR", "EICR"])[0] == "EICR"


def test_two_of_three_carries_it():
    # The quorum, and the case the old single sample got right only by luck.
    assert vote(["CG_2382", "FIRE_ALARM_SVC", "CG_2382"])[0] == "CG_2382"


def test_a_three_way_split_answers_nothing():
    # This is the observed failure, and returning any one of the three would be picking a
    # coin-flip. None hands it to the keyword heuristic, which is deterministic and — on the
    # document that produced this split — right.
    assert vote(["CG_2382", "FIRE_ALARM_SVC", "HSE_POLICY"])[0] is None


def test_a_failed_pass_does_not_sink_the_vote():
    # Two agreeing answers still make a majority when the third call errors.
    assert vote(["EICR", RuntimeError("timeout"), "EICR"])[0] == "EICR"


def test_one_answer_out_of_three_is_not_a_majority():
    # Two passes returning nothing is not agreement with the one that answered.
    assert vote([None, "EICR", None])[0] is None


def test_no_answers_at_all_is_none():
    assert vote([None, None, None])[0] is None


@pytest.mark.parametrize("answers", [["A", "A", "B"], ["B", "A", "A"], ["A", "B", "A"]])
def test_the_result_does_not_depend_on_which_pass_answered_first(answers: list[str]):
    # Order independence is the point: if the winner moved with the order the calls happened
    # to return in, the vote would just be reintroducing the randomness it exists to remove.
    assert vote(answers)[0] == "A"
