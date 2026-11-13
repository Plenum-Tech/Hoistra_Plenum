"""Notes on a turn that was asked on another machine and copied into Trace Lab (14 Nov 2026).

Such a turn is in the eval store (tracelab.sessions / traces / observations) and not in the chat's own tables, so
conversations.get_turn answers None and the session page said "The chat no longer has this turn, so notes cannot be
added here." A reviewer must be able to annotate it exactly as a chat-recorded turn: notes are step_labels rows keyed
by the turn id, with keys derived from the stored observations, and the chat's rows are never faked.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member, sample_turn, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, labelling
from trace_lab.review_errors import Invalid, NotFound

pytestmark = requires_scratch

SID = "scn__frontend_nov13_202718__frontend-pilot-030__1"
TID = "turn-copied-1"


async def _stored(lab):
    await seed_session(lab, SID, tag="frontend_nov13_202718", turn_ids=(TID,))
    return labelling.for_org(member(), ORG)


async def _keys(m):
    got = await eval_store.trace_notes(m, TID)
    rec = got["record"]
    nodes = (await eval_store.trace_tree_of(m, TID))["nodes"]
    return got, rec, nodes


async def test_a_copied_turn_has_a_record_the_page_can_annotate_on(lab_scratch, main_scratch):
    m = await _stored(lab_scratch)
    got, rec, nodes = await _keys(m)
    assert rec is not None and rec["id"] == TID and got["labels"] == []
    roles = [x["role"] for x in rec["messages"]]
    assert roles[0] == "user" and "assistant" in roles
    assert next(x for x in rec["messages"] if x["role"] == "user")["text"].startswith("Which certificates")
    tool = next(n for n in nodes if n["type"] == "tool")
    assert any(x.get("span_id") == tool["id"] for x in rec["messages"])
    agent = next(n for n in nodes if n["type"] == "agent" and n["parent_id"])
    assert any(g.get("span_id") == agent["id"] for g in rec["groups"])


async def test_a_note_on_a_copied_turn_is_saved_listed_edited_and_deleted(lab_scratch, main_scratch):
    m = await _stored(lab_scratch)
    _, rec, nodes = await _keys(m)
    tool = next(n for n in nodes if n["type"] == "tool")
    agent = next(n for n in nodes if n["type"] == "agent" and n["parent_id"])
    on_tool = next(x["id"] for x in rec["messages"] if x.get("span_id") == tool["id"])
    keys = [on_tool, on_tool + "#output", f"{TID}:{agent['id']}:agent", f"{TID}:{agent['id']}:agent#output",
            f"{TID}:scenario",
            next(x["id"] for x in rec["messages"] if x["role"] == "user"),
            next(x["id"] for x in rec["messages"] if x["role"] == "assistant")]
    saved = []
    for i, k in enumerate(keys):
        out = await labelling.save_label(m, TID, step_key=k, label=None, note=f"note {i}", step_title=f"t{i}",
                                         quote="timeout" if i == 1 else None)
        assert out["turn_id"] == TID and out["step_key"] == k and out["conversation_key"] == SID
        saved.append(out)
    listed = await eval_store.trace_notes(m, TID)
    assert [x["note"] for x in listed["labels"]] == [f"note {i}" for i in range(len(keys))]
    assert listed["labels"][1]["quote"] == "timeout"
    first = saved[0]
    edited = await labelling.save_label(m, TID, step_key=None, label=None, note="changed", step_title=None,
                                        lineage_id=first["lineage_id"], base=first["id"])
    assert edited["edited"] is True and edited["step_key"] == first["step_key"]
    await labelling.clear_label(m, TID, lineage_id=first["lineage_id"], base=edited["id"])
    left = [x["note"] for x in (await eval_store.trace_notes(m, TID))["labels"]]
    assert "changed" not in left and len(left) == len(keys) - 1
    # They are step_labels rows keyed by the turn, so the Notes page lists them.
    assert len((await labelling.list_labels(m, limit=100, offset=0))["labels"]) == len(keys) - 1


async def test_notes_written_together_on_a_copied_turn_are_saved_and_removed_together(lab_scratch, main_scratch):
    m = await _stored(lab_scratch)
    _, rec, nodes = await _keys(m)
    tool = next(n for n in nodes if n["type"] == "tool")
    key = next(x["id"] for x in rec["messages"] if x.get("span_id") == tool["id"])
    rows = await labelling.save_group(m, TID, label="wrong", note="both bad", items=[
        {"step_key": key}, {"step_key": key + "#output"}])
    assert len(rows) == 2
    out = await labelling.clear_group(m, TID, group_id=rows[0]["group_id"])
    assert out["removed"] == 2


async def test_no_chat_rows_are_faked_for_a_copied_turn(lab_scratch, main_scratch):
    m = await _stored(lab_scratch)
    await labelling.save_label(m, TID, step_key=f"{TID}:scenario", label=None, note="whole turn", step_title="Turn")
    async with lab_scratch.connect() as c:
        n = (await c.execute(text("SELECT (SELECT count(*) FROM tracelab.evidence_turns)"
                                  " + (SELECT count(*) FROM tracelab.evidence_spans)"))).scalar_one()
    assert n == 0


async def test_a_step_that_is_not_in_the_copied_turn_is_refused(lab_scratch, main_scratch):
    m = await _stored(lab_scratch)
    with pytest.raises(Invalid):
        await labelling.save_label(m, TID, step_key="not-a-step-of-this-turn", label=None, note="x", step_title="x")


async def test_a_member_of_another_company_cannot_read_or_write_notes_on_a_copied_turn(lab_scratch, main_scratch):
    await _stored(lab_scratch)
    outsider = member(org=OTHER_ORG, role="admin", all_companies=False)
    with pytest.raises(NotFound):
        await eval_store.trace_notes(outsider, TID)
    with pytest.raises(NotFound):
        await labelling.save_label(outsider, TID, step_key=f"{TID}:scenario", label=None, note="x", step_title="x")
    with pytest.raises(NotFound):
        await labelling.save_label(labelling.for_org(member(), OTHER_ORG), TID, step_key=f"{TID}:scenario",
                                   label=None, note="x", step_title="x")


async def test_a_turn_that_is_nowhere_still_cannot_be_annotated(lab_scratch, main_scratch):
    m = labelling.for_org(member(), ORG)
    with pytest.raises(NotFound):
        await eval_store.trace_notes(m, "no-such-turn")
    with pytest.raises(NotFound) as e:
        await labelling.save_label(m, "no-such-turn", step_key="no-such-turn:scenario", label=None, note="x",
                                   step_title="x")
    assert "turn_not_found" in str(e.value)


async def test_a_turn_the_chat_recorded_still_uses_the_chat_record(lab_scratch, main_scratch):
    from test_trace_lab_eval_store_sql import _main_turn

    await seed_session(lab_scratch, SID, tag="frontend_nov13_202718", turn_ids=("turn-chat",))
    await _main_turn(main_scratch, *sample_turn("turn-chat", SID))
    m = labelling.for_org(member(), ORG)
    got = await eval_store.trace_notes(m, "turn-chat")
    assert got["record"] is not None and not got["record"].get("stored")
    asked = next(x for x in got["record"]["messages"] if x["role"] == "user")
    assert asked.get("origin") is not None or "channel" in asked        # the chat's normalized message, not a stored one
    out = await labelling.save_label(m, "turn-chat", step_key=str(asked["id"]), label=None, note="n", step_title="t")
    assert out["step_key"] == str(asked["id"])
    async with lab_scratch.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar_one() == 1
