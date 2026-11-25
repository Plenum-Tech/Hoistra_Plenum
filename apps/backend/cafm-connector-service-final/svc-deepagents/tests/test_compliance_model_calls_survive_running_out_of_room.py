"""A compliance model call that runs out of room is not the end of the answer (agents/orchestrator.py).

Production traces, 27 Oct - 16 Nov 2026:

- the analyst (claude-sonnet-5, max_tokens 6000) lost 13 answers. Some replies were empty - every token
  spent thinking ({"raw": "", "stop_reason": "max_tokens"}, "Expecting value: line 1 column 1"); most were
  cut deep inside the JSON ("Unterminated string starting at: line 1 column 9684"), after the narrative and
  the tiles had been written in full. The analyst is asked once more with less thinking and more room
  (2 Nov); when that reply is cut too, the zones the model finished are kept, and a zone it did not finish
  is left out - never completed by guessing (agents/lenient_json.py says why).
- the reviewer (params {"effort": "medium", "max_tokens": 4000}) returned "" on 9 Nov: 4,000 of 4,000
  tokens spent thinking. A review that does not come back is not fatal - the answer is shown as written -
  but the review is what catches a wrong answer, so a reply that ran out of room is asked for once more
  with less thinking and twice the room.
- the planner (gpt-5.6-terra) returned a plan cut at 200 output tokens on 28 Oct, inside its second
  sub-question, and the turn fell back to "read the whole register". Its call named no ceiling of its
  own, so the endpoint's applied; it now names one a full plan fits in.
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from src.agents import activity_log
from src.agents import orchestrator as orch
from src.llm_factory import token_limit_kwargs

O = orch.DeepAgentOrchestrator


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(orch.settings, "anthropic_api_key", "test-key", raising=False)
    monkeypatch.setattr(activity_log, "fire", lambda **_kw: None)
    monkeypatch.setattr(orch.llm_cost, "record", lambda *a, **k: None)


def _reply(text: str, stop: str, out: int = 6000) -> SimpleNamespace:
    content = [SimpleNamespace(type="thinking", thinking="...")]
    if text:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=152918, output_tokens=out))


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


@pytest.fixture
def anthropic_replies(monkeypatch):
    """Anthropic replaced by `replies`, one per call, by stream or by create; returns (replies, calls)."""
    calls: list[dict] = []
    replies: list[SimpleNamespace] = []

    class _Messages:
        def stream(self, **kw):
            calls.append(kw)
            return _Stream(replies[len(calls) - 1])

        async def create(self, **kw):
            calls.append(kw)
            return replies[len(calls) - 1]

    class _Client:
        def __init__(self, api_key: str) -> None:
            self.messages = _Messages()

    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=_Client))
    return replies, calls


# ── the analyst ──────────────────────────────────────────────────────────────────────────────────

NARRATIVE = "Bishopsgate Tower (site B-301) has 29 certificates; 3 have lapsed."
KPIS = [{"count": 3, "label": "Lapsed or expired", "severity": "critical"}]
#: Cut inside the narrative, as on 2 Nov ("Unterminated string starting at: line 1 column 14").
CUT_IN_NARRATIVE = ('{"narrative":"Bishopsgate Tower (site B-301) has 29 certificates. The fire risk '
                    'assessment lapsed 5 days ago,')
#: The narrative and the tiles written in full, cut inside the groups (column 9684 on 6 Nov).
CUT_IN_GROUPS = (json.dumps({"narrative": NARRATIVE, "kpis": KPIS})[:-1]
                 + ', "groups": [{"title": "Fire safety", "items": [{"label": "FRA - lapsed 5 da')


def _analyse(replies, *given):
    replies.extend(given)
    return asyncio.run(O._claude_analyse_compliance(object.__new__(O), "q", "{}"))


def test_a_retry_that_is_cut_too_keeps_the_zones_the_model_finished(anthropic_replies):
    replies, calls = anthropic_replies
    out = _analyse(replies, _reply(CUT_IN_NARRATIVE, "max_tokens"),
                   _reply(CUT_IN_GROUPS, "max_tokens", out=12000))
    assert len(calls) == 2
    assert out == {"narrative": NARRATIVE, "kpis": KPIS}, "the cut groups zone is left out, not guessed"


def test_the_first_cut_is_kept_when_the_retry_comes_back_empty(anthropic_replies):
    replies, _calls = anthropic_replies
    out = _analyse(replies, _reply(CUT_IN_GROUPS, "max_tokens"), _reply("", "max_tokens", out=12000))
    assert out == {"narrative": NARRATIVE, "kpis": KPIS}


def test_a_narrative_cut_mid_sentence_is_never_completed(anthropic_replies):
    replies, calls = anthropic_replies
    assert _analyse(replies, _reply(CUT_IN_NARRATIVE, "max_tokens"),
                    _reply(CUT_IN_NARRATIVE, "max_tokens", out=12000)) is None
    assert len(calls) == 2


def test_a_reply_that_was_not_cut_is_not_salvaged(anthropic_replies):
    """Only a reply that ran out of room is read for its finished zones; a reply the model ended
    itself and that still does not parse is not an answer cut short."""
    replies, _calls = anthropic_replies
    broken = json.dumps({"narrative": NARRATIVE})[:-1] + ', "kpis": [oops]}'
    assert _analyse(replies, _reply(broken, "end_turn"), _reply(broken, "end_turn")) is None


# ── the reviewer ─────────────────────────────────────────────────────────────────────────────────

def _review(replies, *given):
    replies.extend(given)
    return asyncio.run(O._review_compliance_answer(
        object.__new__(O), "Which buildings are out of compliance today?",
        {"narrative": NARRATIVE, "kpis": KPIS}, {}, []))


def test_a_review_that_runs_out_of_room_is_asked_once_more_with_less_thinking(anthropic_replies):
    replies, calls = anthropic_replies
    out = _review(replies, _reply("", "max_tokens", out=4000), _reply('{"verdict": "pass"}', "end_turn"))
    assert out == {"verdict": "pass"}
    assert len(calls) == 2
    first, second = calls
    assert second["output_config"]["effort"] == "low"
    assert second["max_tokens"] == 2 * first["max_tokens"]
    assert second["messages"] == first["messages"], "the same answer is reviewed"


def test_the_review_is_asked_at_most_twice(anthropic_replies):
    replies, calls = anthropic_replies
    assert _review(replies, _reply("", "max_tokens", out=4000), _reply("", "max_tokens", out=8000),
                   _reply('{"verdict": "pass"}', "end_turn")) is None
    assert len(calls) == 2


def test_a_review_unreadable_for_another_reason_is_not_asked_again(anthropic_replies):
    """Room is the failure seen; anything else stays as before - no review, answer shown as written."""
    replies, calls = anthropic_replies
    assert _review(replies, _reply("I cannot review this.", "end_turn", out=40),
                   _reply('{"verdict": "pass"}', "end_turn")) is None
    assert len(calls) == 1


# ── the planner ──────────────────────────────────────────────────────────────────────────────────

def _largest_plan() -> str:
    """The planner's JSON at its largest: five parts (the most kept), each with filters and a sort."""
    part = {"id": "q5", "text": "Which assets at Bishopsgate Tower carry an insurance risk flag and "
                                "which of their certificates expire before the end of next quarter?",
            "query": {"scope": "both",
                      "filters": [{"field": "building_name", "op": "eq", "value": "Bishopsgate Tower"},
                                  {"field": "insurance_risk_flag", "op": "eq", "value": True},
                                  {"field": "expiry_date", "op": "lte", "value": "2027-03-31"},
                                  {"field": "status", "op": "in", "value": ["Current", "Expiring"]}],
                      "logic": "and", "sort": {"field": "expiry_date", "dir": "asc"}}}
    return json.dumps({"reason": "Identify risk-flagged assets at Bishopsgate Tower and the remedial "
                                 "items to maintain to avoid extra cost.",
                       "needs": ["regulations", "coverage", "work_orders"],
                       "sub_questions": [part] * 5})


class _Planner:
    model_name = "gpt-5.6-terra"

    def __init__(self) -> None:
        self.bound: list[dict] = []

    def bind(self, **kw):
        self.bound.append(kw)
        return self

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=_largest_plan())


def _plan(monkeypatch, llm):
    o = O.__new__(O)
    o._llm = llm

    async def _vocab(self):
        return {}

    monkeypatch.setattr(O, "_compliance_value_vocabulary", _vocab)
    return asyncio.run(O._plan_compliance_query(o, "which assets are under risk at bishopsgate tower"))


def test_the_planner_is_called_with_room_for_its_largest_plan(monkeypatch):
    llm = _Planner()
    plan = _plan(monkeypatch, llm)
    assert len(plan["sub_questions"]) == 5
    assert len(llm.bound) == 1, "the planner call names its own output ceiling"
    (limit,) = llm.bound[0].values()
    assert llm.bound[0] == token_limit_kwargs("gpt-5.6-terra", limit), "named as this model wants it"
    # JSON runs at roughly three characters a token; twice that leaves the model room to spare.
    assert limit >= 2 * len(_largest_plan()) // 3
    assert limit > 200
