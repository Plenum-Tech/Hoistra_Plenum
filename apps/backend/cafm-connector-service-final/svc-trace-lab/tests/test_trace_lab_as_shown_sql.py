"""The answer as Hoistra showed it (16 Nov 2026, Hussain: "make sure u show me the exact trace steps and exact same ans
in the trace lab"). A trace keeps only the answer's text; the cards and the OpenUI layout are on the chat's own row of the
turn (plenum_cafm.chat_turns.tools). GET /eval/traces/{turn_id}/as-shown reads that row, read-only, for the trace's own
company and session: the row asked nearest the trace's start."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, T0, member, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import shown_answer
from trace_lab.review_errors import NotFound

pytestmark = [requires_scratch, pytest.mark.asyncio]

SID = "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f71"
CARDS = {"tool": "compliance_response", "output": {"narrative": "Bishopsgate.", "kpis": [{"count": 48353, "label": "£/yr at stake"}]}}
VIEW = {"tool": "openui_view", "output": {"v": 1, "lang": "root = Stack([])"}}


async def _chat_turn(main, turn_no: int, *, at, answer: str, tools: list, org: str = ORG, thread: str = SID) -> None:
    async with main.begin() as c:
        await c.execute(text(
            "INSERT INTO plenum_cafm.chat_turns (id, thread_id, turn_no, organization_id, question, answer, tools, created_at,"
            " answered_at) VALUES (gen_random_uuid(), :t, :n, CAST(:org AS uuid), 'Q', :a, CAST(:tools AS jsonb), :at, :at)"),
            {"t": thread, "n": turn_no, "org": org, "a": answer, "tools": json.dumps(tools), "at": at})


async def test_each_turn_gets_its_own_cards_and_layout(lab_scratch, main_scratch):
    await seed_session(lab_scratch, SID, tag="chat", turn_ids=("turn-x", "turn-y"))      # turns at T0 and T0 + 1 min
    await _chat_turn(main_scratch, 1, at=T0, answer="First answer.", tools=["answer_from_records", CARDS])
    await _chat_turn(main_scratch, 2, at=T0 + timedelta(minutes=1), answer="Second answer.", tools=[VIEW])
    x = await shown_answer.as_shown(member(), "turn-x")
    assert x["found"] is True and x["answer"] == "First answer." and x["tools"] == ["answer_from_records", CARDS]
    y = await shown_answer.as_shown(member(), "turn-y")
    assert y["found"] is True and y["answer"] == "Second answer." and y["tools"] == [VIEW]


async def test_another_companys_row_is_never_read(lab_scratch, main_scratch):
    await seed_session(lab_scratch, SID, tag="chat", turn_ids=("turn-x",))
    await _chat_turn(main_scratch, 1, at=T0, answer="Not ours.", tools=[CARDS], org=OTHER_ORG)
    assert (await shown_answer.as_shown(member(), "turn-x"))["found"] is False
    with pytest.raises(NotFound):
        await shown_answer.as_shown(member(org=OTHER_ORG, all_companies=False, role="admin"), "turn-x")


async def test_no_chat_row_or_one_from_another_time_is_not_shown(lab_scratch, main_scratch):
    await seed_session(lab_scratch, SID, tag="chat", turn_ids=("turn-x",))
    assert (await shown_answer.as_shown(member(), "turn-x"))["found"] is False
    await _chat_turn(main_scratch, 1, at=T0 + timedelta(hours=3), answer="Hours later.", tools=[CARDS])
    assert (await shown_answer.as_shown(member(), "turn-x"))["found"] is False


async def test_a_trace_not_in_trace_lab(lab_scratch, main_scratch):
    with pytest.raises(NotFound):
        await shown_answer.as_shown(member(), "turn-nowhere")


async def test_the_route(lab_scratch, main_scratch):
    from trace_lab import routes_eval

    await seed_session(lab_scratch, SID, tag="chat", turn_ids=("turn-x",))
    await _chat_turn(main_scratch, 1, at=T0, answer="First answer.", tools=[CARDS])
    got = await routes_eval.eval_trace_as_shown("turn-x", m=member())
    assert got["ok"] is True and got["found"] is True and got["tools"] == [CARDS]


async def test_a_turn_with_no_answer_of_its_own_never_borrows_the_next_ones(lab_scratch, main_scratch):
    await seed_session(lab_scratch, SID, tag="chat", turn_ids=("turn-x",))
    async with main_scratch.begin() as c:
        await c.execute(text(
            "INSERT INTO plenum_cafm.chat_turns (id, thread_id, turn_no, organization_id, question, created_at)"
            " VALUES (gen_random_uuid(), :t, 1, CAST(:org AS uuid), 'Q', :at)"), {"t": SID, "org": ORG, "at": T0})
    await _chat_turn(main_scratch, 2, at=T0 + timedelta(seconds=90), answer="The next turn's.", tools=[CARDS])
    assert (await shown_answer.as_shown(member(), "turn-x"))["found"] is False
