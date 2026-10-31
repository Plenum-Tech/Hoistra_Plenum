"""A sub-agent that returned nothing, and said nothing about it.

Every run of the meter question recorded the same line:

    TOOL: task   input: {"agent": "energy_intelligence", ...}   output: null

The answers survived only because the udr sub-agent, fired in the same millisecond, returned
the right figures. energy_intelligence — the agent select_skill named PRIMARY with
confidence "high" — contributed nothing, four runs out of four.

The logs showed meta.task at the start and then nothing for that agent: no llm.call, which
is recorded after ainvoke returns, and no meta.task.error, which the except branch writes.
It neither finished nor failed. A cancelled coroutine does exactly that, and CancelledError
has derived from BaseException since Python 3.8 — so `except Exception` never saw it, the
tool returned None, and the parent wrote `null` into the evidence.

A null in the evidence is indistinguishable, to the model reading it, from a tool that was
never called. That is how "no answer from this source" became "no data for this building".
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.agents import meta_tools


class Runner:
    """Stands in for the task runner, returning whatever the test needs."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    async def run(self, agent: str, prompt: str):
        self.calls.append((agent, prompt))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def call(answer, agent="energy_intelligence", monkeypatch=None):
    monkeypatch.setattr(meta_tools, "_task_runner", Runner(answer))
    return asyncio.run(meta_tools.task.ainvoke({"agent": agent, "prompt": "do a thing"}))


def test_a_real_answer_is_passed_through_untouched(monkeypatch):
    out = call("Riverside Court has 50 readings totalling 923.92 kWh.", monkeypatch=monkeypatch)
    assert out == "Riverside Court has 50 readings totalling 923.92 kWh."


def test_none_becomes_a_stated_failure_not_a_null(monkeypatch):
    # The regression. This returned None, and the parent recorded output: null.
    out = call(None, monkeypatch=monkeypatch)
    assert out is not None and isinstance(out, str)
    body = json.loads(out)
    assert "energy_intelligence" in body["error"]


@pytest.mark.parametrize("empty", ["", "   ", "\n"])
def test_a_blank_answer_is_treated_the_same_way(empty, monkeypatch):
    # Blank is the same hole as null to anything reading the evidence.
    body = json.loads(call(empty, monkeypatch=monkeypatch))
    assert body["agent"] == "energy_intelligence"


def test_the_failure_names_the_agent_that_produced_nothing(monkeypatch):
    body = json.loads(call(None, agent="udr", monkeypatch=monkeypatch))
    assert body["agent"] == "udr" and "udr" in body["error"]


def test_the_model_is_told_this_is_missing_evidence_not_absent_data():
    """The distinction the null destroyed.

    A source that did not respond is not a source that found nothing, and an answer built on
    the second when the first is true reports "no data for Riverside Court" about a building
    with 50 readings.
    """
    source = open(meta_tools.__file__, encoding="utf-8").read()
    guidance = source[source.index('"guidance": ('):]
    guidance = guidance[: guidance.index("})")]
    assert "missing evidence" in guidance
    assert "not as an absence of data" in guidance


def test_cancellation_is_re_raised_rather_than_swallowed():
    """Logged and re-raised, not caught and hidden.

    Swallowing a CancelledError leaves the task pretending to have completed and breaks the
    cancellation it belongs to. What was missing was never the handling — only the record
    that it had happened.
    """
    source = open(meta_tools.__file__, encoding="utf-8").read()
    block = source[source.index("except asyncio.CancelledError:"):]
    block = block[: block.index("except Exception")]
    assert "meta.task.cancelled" in block
    assert "raise" in block
    assert "elapsed_ms" in block, "how long it ran is the number that makes this diagnosable"


def test_the_cancellation_handler_comes_before_the_general_one():
    # After `except Exception` it would be unreachable for the opposite reason to the bug:
    # CancelledError is not an Exception, so ordering is what makes it catchable at all.
    source = open(meta_tools.__file__, encoding="utf-8").read()
    assert source.index("except asyncio.CancelledError:") < source.index(
        'log.error("meta.task.error"'
    )


def test_the_elapsed_clock_starts_outside_the_try():
    """Otherwise a cancellation arriving before the try's first statement raises NameError
    inside the handler — replacing a silent null with a silent crash.

    Scoped to the window around the assignment: the module has several try blocks, and
    comparing against the first one in the file tests nothing about this one.
    """
    source = open(meta_tools.__file__, encoding="utf-8").read()
    at = source.index("_t0 = time.perf_counter()")
    window = source[at:at + 400]
    assert "try:" in window, "_t0 is not immediately above the try it must outlive"
    assert window.index("try:") < window.index("ainvoke")


def test_an_uninitialised_runner_still_returns_a_string(monkeypatch):
    monkeypatch.setattr(meta_tools, "_task_runner", None)
    out = asyncio.run(meta_tools.task.ainvoke({"agent": "udr", "prompt": "x"}))
    assert isinstance(out, str) and "not initialised" in out.lower()
