"""A compliance answer is not lost to one slow or empty step (agents/orchestrator.py).

2 Nov 2026, "What certificates does Bishop Gate Tower have?" on a freshly started stack took 19
minutes and came back as plain text:

- the certificate-reach read waited 15 minutes for the ontology to be built from cold (the first
  question after a restart), with nothing bounding it, while the chat showed "Compliance analyst
  reasoning" the whole time;
- the analyst then spent all 6,000 of its output tokens thinking over 152,918 input tokens and
  returned no text ("Expecting value: line 1 column 1 (char 0)"), so the typed answer - the cards -
  was lost and the plain-text summary stood in.

The reach read is best effort, so it now has a time limit and the ontology keeps building for the
next question; the analyst is asked once more, with less thinking and more room, when its reply
cannot be read.
"""
from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from src.agents import activity_log
from src.agents import ontology_qa as oq
from src.agents import orchestrator as orch

O = orch.DeepAgentOrchestrator


# ── the analyst ──────────────────────────────────────────────────────────────────────────────────

class _Stream:
    def __init__(self, message: SimpleNamespace) -> None:
        self.message = message

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration

    async def get_final_message(self) -> SimpleNamespace:
        return self.message


def _reply(text: str, stop: str) -> SimpleNamespace:
    content = [SimpleNamespace(type="thinking", thinking="...")]
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=152918, output_tokens=6000))


@pytest.fixture
def analyst(monkeypatch):
    """The analyst with Anthropic replaced by `replies`, one per call; returns (run, calls)."""
    calls: list[dict] = []
    replies: list[SimpleNamespace] = []

    class _Messages:
        def stream(self, **kw):
            calls.append(kw)
            return _Stream(replies[len(calls) - 1])

    class _Client:
        def __init__(self, api_key: str) -> None:
            self.messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=_Client))
    monkeypatch.setattr(orch.settings, "anthropic_api_key", "test-key", raising=False)
    monkeypatch.setattr(activity_log, "fire", lambda **_kw: None)
    monkeypatch.setattr(orch.llm_cost, "record", lambda *a, **k: None)

    def run(*given: SimpleNamespace):
        replies.extend(given)
        return asyncio.run(O._claude_analyse_compliance(object.__new__(O), "q", "{}"))

    return run, calls


def test_an_analyst_that_runs_out_of_room_is_asked_once_more_with_less_thinking(analyst):
    run, calls = analyst
    out = run(_reply("", "max_tokens"), _reply('{"narrative": "Bishopsgate Tower carries 10."}', "end_turn"))
    assert out == {"narrative": "Bishopsgate Tower carries 10."}
    assert len(calls) == 2
    first, second = calls
    assert second["output_config"]["effort"] == "low"
    assert second["max_tokens"] == 2 * first["max_tokens"]


def test_an_unreadable_reply_for_another_reason_is_retried_at_the_same_setting(analyst):
    run, calls = analyst
    out = run(_reply("Sorry, here is the answer:", "end_turn"), _reply('{"narrative": "ok"}', "end_turn"))
    assert out == {"narrative": "ok"}
    assert calls[1]["output_config"]["effort"] == calls[0]["output_config"]["effort"]
    assert calls[1]["max_tokens"] == calls[0]["max_tokens"]


def test_the_analyst_is_asked_at_most_twice(analyst):
    run, calls = analyst
    assert run(_reply("", "max_tokens"), _reply("", "max_tokens"), _reply('{"narrative": "x"}', "end_turn")) is None
    assert len(calls) == 2


# ── what the certificates reach ─────────────────────────────────────────────────────────────────

def test_a_slow_reach_read_is_given_up_and_left_to_finish(monkeypatch):
    finished: list[bool] = []

    async def slow(ids, principal=None, budget=0):
        await asyncio.sleep(0.3)
        finished.append(True)
        return {"ok": True, "certificates_reaching_something": 1, "assets": 1, "records": "{}"}

    monkeypatch.setattr(oq, "linked_for_certificates", slow)
    monkeypatch.setattr(orch, "CERTIFICATE_REACH_TIMEOUT_S", 0.05)

    async def ask():
        t0 = time.perf_counter()
        call = await O._certificate_reach_call(None, [{"id": "c1"}], "s1")
        waited = time.perf_counter() - t0
        await asyncio.sleep(0.4)  # the read itself carries on (it is what warms the ontology)
        return call, waited

    call, waited = asyncio.run(ask())
    assert call is None and waited < 0.25
    assert finished == [True]


def test_the_reach_read_has_a_time_limit_of_seconds_not_minutes():
    assert 5 <= orch.CERTIFICATE_REACH_TIMEOUT_S <= 60
