"""Golden conversations end to end on the scratch databases: a golden run made by the runner (the chat a
recorded stand-in) is read back from Trace Lab's own evidence tables by trace_lab.conversations and
normalized with the same normalize_turn as live turns; list_runs adds it beside the live pseudo-run; nothing
crosses companies and the expected answer never appears. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from golden_runner_env import BEARER, EXPECTED, ORG, Msg, body, drain, install_seeded, principal
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import conversations as cv
from trace_lab import runner

pytestmark = requires_scratch

OTHER = "00000000-0000-0000-0000-0000000000b2"


@pytest.fixture
async def golden_run(lab_scratch, monkeypatch):
    """A finished golden run: case 21 (question + follow-up) and case 22, one rep each."""
    w = await install_seeded(lab_scratch, monkeypatch)
    w.script += [Msg(answer="759 work orders."), Msg(answer="733 of them were reactive."),
                 Msg(answer="Two vendors: Apex and Brightwater.")]
    run = (await runner.start_run(org=ORG, principal=principal(), authorization=BEARER, body=body()))["run"]
    for _ in range(3):
        await runner.step(run["id"], principal=principal(), authorization=BEARER)
        await drain()
    return run


def _f(run_id: str, **kw):
    return cv.make_filter(run=run_id, **kw)


async def test_a_golden_run_lists_one_conversation_per_case_and_rep(golden_run):
    out = await cv.list_conversations(principal(), ORG, _f(golden_run["id"]), cursor=None, limit=50)
    assert out["total"] == 2 and out["next_cursor"] is None
    by_key = {c["golden"]["case_key"]: c for c in out["conversations"]}
    c21 = by_key["21"]
    assert c21["key"] == "s:" + runner.session_id_for(golden_run["id"], "21", 1)
    assert (c21["session_kind"], c21["channels"], c21["channel_sources"]) == ("synthetic", ["golden"], ["recorded"])
    assert c21["session_label"] == "Golden run · 21 · rep 1" and c21["turns_in_window"] == 2
    assert c21["run_id"] == golden_run["id"] and c21["first_question"].startswith("How many work orders")
    assert c21["models"] and c21["cost_usd"] > 0 and c21["asker"]["email"] == "admin@example.com"
    assert EXPECTED not in json.dumps(out)


async def test_paging_and_filters_work_on_golden_conversations(golden_run):
    first = await cv.list_conversations(principal(), ORG, _f(golden_run["id"]), cursor=None, limit=1)
    assert len(first["conversations"]) == 1 and first["next_cursor"]
    second = await cv.list_conversations(principal(), ORG, _f(golden_run["id"]), cursor=first["next_cursor"], limit=1)
    assert {first["conversations"][0]["key"], second["conversations"][0]["key"]} == {
        "s:" + runner.session_id_for(golden_run["id"], k, 1) for k in ("21", "22")}
    hit = await cv.list_conversations(principal(), ORG, _f(golden_run["id"], q="reactive"), cursor=None, limit=50)
    assert [c["golden"]["case_key"] for c in hit["conversations"]] == ["21"] and hit["conversations"][0]["matching_turns"] == 1
    for kw in ({"channel": "chat"}, {"status": "error"}, {"session_kind": "observed"}):
        assert (await cv.list_conversations(principal(), ORG, _f(golden_run["id"], **kw), cursor=None,
                                            limit=50))["total"] == 0


async def test_a_golden_conversation_is_normalized_like_a_live_one(golden_run):
    key = "s:" + runner.session_id_for(golden_run["id"], "21", 1)
    conv = await cv.get_conversation(principal(), ORG, key)
    assert conv["run"] == {"run_id": golden_run["id"], "kind": "golden"} and conv["turn_count"] == 2
    assert [h["turn_index"] for h in conv["turns"]] == [1, 2] and all(h["channel"] == "golden" for h in conv["turns"])
    active = conv["active"]
    assert active["input"].startswith("How many work orders") and active["output"] == "759 work orders."
    assert active["meta"]["golden"] == {"run_id": golden_run["id"], "case_key": "21", "rep": 1}
    assert active["meta"]["channel_source"] == "recorded" and active["meta"]["organization_id"] == ORG
    assert any(m["role"] == "tool_call" for m in active["messages"])
    follow = await cv.get_conversation(principal(), ORG, key, turn_id=conv["turns"][1]["turn_id"])
    assert follow["active"]["output"] == "733 of them were reactive." and follow["active"]["meta"]["turn_index"] == 2
    assert EXPECTED not in json.dumps(conv) and EXPECTED not in json.dumps(follow)
    turn = await cv.get_turn(principal(), ORG, conv["turns"][1]["turn_id"])
    assert turn["conversation_key"] == key and turn["trace"]["id"] == conv["turns"][1]["turn_id"]


async def test_updates_and_facets_count_golden_turns(golden_run):
    upd = await cv.conversation_updates(principal(), ORG, _f(golden_run["id"]),
                                        watermark=datetime.now(timezone.utc) - timedelta(hours=1))
    assert (upd["new_turns"], upd["new_conversations"]) == (3, 2)
    fac = await cv.facets(principal(), ORG, _f(golden_run["id"]))
    assert fac["channels"] == [{"value": "golden", "turns": 3}]
    assert fac["session_kinds"] == [{"value": "synthetic", "conversations": 2}] and fac["models"]


async def test_nothing_crosses_companies(golden_run):
    key = "s:" + runner.session_id_for(golden_run["id"], "21", 1)
    assert (await cv.list_conversations(principal(), OTHER, _f(golden_run["id"]), cursor=None, limit=50))["total"] == 0
    assert await cv.get_conversation(principal(), OTHER, key) is None
    conv = await cv.get_conversation(principal(), ORG, key)
    assert await cv.get_turn(principal(), OTHER, conv["turns"][0]["turn_id"]) is None


async def test_list_runs_adds_golden_runs_beside_live_traffic(main_scratch, golden_run):
    now = datetime.now(timezone.utc)
    out = await cv.list_runs(principal(), ORG, since=now - timedelta(days=1), until=now + timedelta(days=1))
    assert out["golden_available"] is True and [r["run_id"] for r in out["runs"]] == ["live", golden_run["id"]]
    g = out["runs"][1]
    assert (g["kind"], g["status"], g["conversation_count"], g["turn_count"]) == ("golden", "done", 2, 3)
    assert g["name"] == golden_run["label"] and g["cost_usd"] > 0 and g["recorded"] is True
    other = await cv.list_runs(principal(), OTHER, since=now - timedelta(days=1), until=now + timedelta(days=1))
    assert [r["run_id"] for r in other["runs"]] == ["live"]
