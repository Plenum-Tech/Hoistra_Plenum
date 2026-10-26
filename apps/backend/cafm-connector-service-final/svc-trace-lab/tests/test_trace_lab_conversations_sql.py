"""Trace Lab conversations against a throwaway Postgres (TRACE_LAB_TEST_DB_URL, database tracelab_scratch only):
the acceptance conversation User -> Model -> Tool -> Model -> Answer, grouping and labels, the paging contract,
exact totals, the update watermark, the read-time channel, model calls, company scoping, deleted threads,
SQL/Python parity and the read-only transaction. Rows are written only to the scratch database that P2-T6's
main_scratch fixture guards; Trace Lab's own code only reads."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.agents import trace
from src.services.principal import Principal
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401  (P2-T6 fixtures and marker)
from trace_lab import conversations as cv
from trace_lab import db
from trace_lab import normalize as n
from trace_lab import provenance

pytestmark = requires_scratch

ORG = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
ADMIN = Principal(user_id=uuid.UUID(USER), email="ada@example.com", organization_id=uuid.UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)
TABLES = ("plenum_cafm.agent_trace_turns", "plenum_cafm.agent_trace_spans", "plenum_cafm.chat_threads",
          "plenum_cafm.agent_activity_log")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture
async def seeded(main_scratch, lab_scratch, monkeypatch):  # noqa: F811
    engine = main_scratch
    assert isinstance(engine, AsyncEngine), "P2-T6's main_scratch must yield the scratch AsyncEngine: stop and report"
    monkeypatch.setattr(db, "main_engine", lambda: engine)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE " + ", ".join(TABLES)))
    return engine


async def add_turn(conn, turn_id, *, session_id, base, minutes_ago=10, created_minutes_ago=None, org=ORG,
                   question="How many boilers?", answer="Four.", ok=True, models=("claude-sonnet-5",), tools=(),
                   cost=0.01, llm_calls=1, tool_calls=0, route=None):
    started = base - timedelta(minutes=minutes_ago)
    created = base - timedelta(minutes=created_minutes_ago) if created_minutes_ago is not None else started + timedelta(seconds=5)
    await conn.execute(text(f"""
        INSERT INTO {trace.TURNS} (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at,
                                   ended_at, latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens,
                                   output_tokens, cache_read_tokens, models, tools, ok, error, created_at)
        VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), 'fm@example.com', :q, :a, :route, :t0, :t1, 1000, :llm,
                :tn, :usd, true, 100, 20, 0, CAST(:models AS jsonb), CAST(:tools AS jsonb), :ok, NULL, :created)"""),
        {"tid": turn_id, "sid": session_id, "org": org, "uid": USER, "q": question, "a": answer, "route": route,
         "t0": started, "t1": started + timedelta(seconds=1), "llm": llm_calls, "tn": tool_calls, "usd": cost,
         "models": json.dumps(list(models)), "tools": json.dumps(list(tools)), "ok": ok, "created": created})


async def add_span(conn, turn_id, span_id, parent, seq, kind, name, *, base, agent="orchestrator", model=None, inp=None,
                   out=None, start=0.0, cost=None, tin=None, tout=None, ok=True, org=ORG):
    t0 = base - timedelta(minutes=10) + timedelta(seconds=start)
    await conn.execute(text(f"""
        INSERT INTO {trace.SPANS} (id, turn_id, session_id, organization_id, parent_id, seq, kind, name, agent, model,
                                   started_at, ended_at, latency_ms, input, output, input_tokens, output_tokens,
                                   cache_read_tokens, cost_usd, ok, error)
        VALUES (CAST(:id AS uuid), :tid, NULL, CAST(:org AS uuid), CAST(:parent AS uuid), :seq, :kind, :name, :agent,
                :model, :t0, :t1, 500, CAST(:inp AS jsonb), CAST(:out AS jsonb), :tin, :tout, 0, :usd, :ok, NULL)"""),
        {"id": span_id, "tid": turn_id, "org": org, "parent": parent, "seq": seq, "kind": kind, "name": name,
         "agent": agent, "model": model, "t0": t0, "t1": t0 + timedelta(seconds=0.5),
         "inp": json.dumps(inp) if inp is not None else None, "out": json.dumps(out) if out is not None else None,
         "tin": tin, "tout": tout, "usd": cost, "ok": ok})


async def add_thread(conn, session_id, title, *, deleted=False, org=ORG):
    await conn.execute(text("""
        INSERT INTO plenum_cafm.chat_threads (id, organization_id, user_id, title, deleted_at)
        VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), :title, CASE WHEN :deleted THEN now() ELSE NULL END)"""),
        {"id": session_id, "org": org, "uid": USER, "title": title, "deleted": deleted})


async def add_activity(conn, session_id, turn_id, payload):
    await conn.execute(text("""
        INSERT INTO plenum_cafm.agent_activity_log (id, session_id, turn_id, agent, stage, direction, payload)
        VALUES (CAST(:id AS uuid), :sid, :tid, 'orchestrator', 'turn', 'input', CAST(:p AS jsonb))"""),
        {"id": str(uuid.uuid4()), "sid": session_id, "tid": turn_id, "p": json.dumps(payload)})


async def keys_of(**kw):
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(**kw), cursor=None, limit=50)
    return [c["key"] for c in out["conversations"]]


async def test_a_real_multi_turn_chat_reads_as_one_conversation(seeded):
    B = _now()
    s = {k: str(uuid.uuid4()) for k in ("root", "prompt", "loop", "m1", "tool", "m2")}
    loop1 = {"messages": 3, "last_human_message": "How many…", "tool_results_since_last_human": 0}
    loop2 = {"messages": 5, "last_human_message": "How many…", "tool_results_since_last_human": 1}
    async with seeded.begin() as conn:
        await add_thread(conn, "chat-1", "Boiler certificates")
        await add_turn(conn, "turn-a1", session_id="chat-1", base=B, minutes_ago=20, question="Which boilers are at B-301?")
        await add_turn(conn, "turn-a2", session_id="chat-1", base=B, minutes_ago=10,
                       question="How many work orders there in August?", answer="758 work orders.",
                       tools=("answer_from_records",), tool_calls=1, llm_calls=2)
        await add_span(conn, "turn-a2", s["root"], None, 0, "turn", "turn", base=B, inp={"message": "How many…"},
                       out={"answer": "758 work orders."})
        await add_span(conn, "turn-a2", s["prompt"], s["root"], 1, "stage", "prompt assembled", base=B, start=0.1,
                       inp={"user_message": "How many…", "system_prompt": "S"}, out={"system_prompt_chars": 1})
        await add_span(conn, "turn-a2", s["loop"], s["root"], 2, "agent", "orchestrator loop", base=B, start=0.2,
                       agent="orchestrator loop")
        await add_span(conn, "turn-a2", s["m1"], s["loop"], 3, "llm", "model", base=B, start=0.2, agent="orchestrator loop",
                       model="claude-sonnet-5", inp=loop1,
                       out={"content": "", "tool_calls": [{"name": "answer_from_records", "args": {}}]},
                       tin=900, tout=40, cost=0.004)
        await add_span(conn, "turn-a2", s["tool"], s["root"], 4, "tool", "answer_from_records", base=B, start=2,
                       agent="tool:wo_engine", inp={"q": "wo"}, out={"total": 758})
        await add_span(conn, "turn-a2", s["m2"], s["loop"], 5, "llm", "model", base=B, start=5, agent="orchestrator loop",
                       model="claude-sonnet-5", inp=loop2, out={"content": "758 work orders.", "tool_calls": []},
                       tin=1100, tout=60, cost=0.005)
    listed = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=50)
    assert listed["total"] == 1
    row = listed["conversations"][0]
    assert (row["key"], row["session_kind"], row["session_label"], row["turns_in_window"]) == \
        ("s:chat-1", "observed", "Boiler certificates", 2)
    assert row["first_question"] == "Which boilers are at B-301?" and row["flags"] == []
    assert row["models"] == ["claude-sonnet-5"] and row["tool_count"] == 1 and row["channel_sources"] == ["inferred"]
    conv = await cv.get_conversation(ADMIN, ORG, "s:chat-1", turn_id="turn-a2")
    assert [h["turn_id"] for h in conv["turns"]] == ["turn-a1", "turn-a2"] and conv["active"]["meta"]["turn_index"] == 2
    order = [m["role"] for m in conv["active"]["messages"] if m["role"] != "system_note"]
    assert order == ["user", "model_call", "tool_call", "tool_result", "model_call", "assistant"]
    meta = conv["active"]["meta"]
    assert (meta["channel"], meta["channel_source"], meta["classifier_version"]) == ("chat", "inferred", "inferred-1")
    assert "inferred_rewrite" not in conv["active"]["flags"]
    one = await cv.get_turn(ADMIN, ORG, "turn-a2")
    assert one["conversation_key"] == "s:chat-1" and one["trace"]["id"] == "turn-a2"
    card = (await cv.get_model_call(ADMIN, ORG, s["m2"]))["model_call"]
    assert card["label"] == "answer synthesis" and card["group"] == "orchestrator loop"


async def test_synthetic_and_inferred_groups_are_labelled_and_the_shared_rerun_split(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-s1", session_id="report-1a2b-1759300000", base=B, minutes_ago=10)
        await add_turn(conn, "turn-s2", session_id="rerun", base=B, minutes_ago=11)
        await add_turn(conn, "turn-s3", session_id="rerun", base=B, minutes_ago=12)
        await add_turn(conn, "turn-s4", session_id=None, base=B, minutes_ago=13)
        await add_turn(conn, "turn-s5", session_id="hs-no-thread", base=B, minutes_ago=14)
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=10)
    got = {c["key"]: (c["session_kind"], c["session_label"]) for c in out["conversations"]}
    assert got == {"s:report-1a2b-1759300000": ("synthetic", "Report card run"),
                   "r:turn-s2": ("synthetic", "Correction re-run"), "r:turn-s3": ("synthetic", "Correction re-run"),
                   "t:turn-s4": ("inferred", "Single turn, no session recorded"),
                   "s:hs-no-thread": ("inferred", "Session not in chat history")}


async def test_channel_filter_and_facets_use_the_read_time_classifier(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-c1", session_id="c-1", base=B, minutes_ago=10,
                       question="Why is the Gas Safety Certificate at B-1 lapsed or blocked, and what should I do next?")
        await add_turn(conn, "turn-c2", session_id="c-2", base=B, minutes_ago=11, question="Migrate B-101 workbook")
        await add_turn(conn, "turn-c3", session_id="c-3", base=B, minutes_ago=12, question="Who services the lifts?")
    assert await keys_of(channel="ui_action") == ["s:c-1"]
    assert await keys_of(channel="upload") == ["s:c-2"]
    assert await keys_of(channel="chat") == ["s:c-3"]
    assert await keys_of(channel="support") == []
    assert await keys_of(channel_source="client") == [] and len(await keys_of(channel_source="inferred")) == 3
    facets = await cv.facets(ADMIN, ORG, cv.make_filter())
    assert {f["value"]: f["turns"] for f in facets["channels"]} == {"ui_action": 1, "upload": 1, "chat": 1}
    assert facets["channel_sources"] == [{"value": "inferred", "turns": 3}]
    assert facets["models"] == [{"value": "claude-sonnet-5", "turns": 3}]
    assert await keys_of(model="claude-sonnet-5") == ["s:c-1", "s:c-2", "s:c-3"] and await keys_of(model="gpt-x") == []


async def test_cursor_pages_without_gaps_or_repeats(seeded):
    B = _now()
    async with seeded.begin() as conn:
        for i in range(7):
            await add_turn(conn, f"turn-p{i}", session_id=f"page-{i}", base=B, minutes_ago=10 + (5 if i == 6 else i))
    seen, cursor, pages = [], None, 0
    while True:
        out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=cursor, limit=3)
        seen += [c["key"] for c in out["conversations"]]
        pages += 1
        assert out["total"] == 7
        cursor = out["next_cursor"]
        if not cursor:
            break
    assert pages == 3 and len(seen) == 7 and len(set(seen)) == 7      # page-5 and page-6 tie on time


async def test_total_is_exact_and_comes_from_the_same_grouping(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-t1", session_id="t-1", base=B, minutes_ago=30, question="Gas certificates at B-101?")
        await add_turn(conn, "turn-t2", session_id="t-1", base=B, minutes_ago=29, question="And the boilers?")
        await add_turn(conn, "turn-t3", session_id="t-1", base=B, minutes_ago=28, question="Boilers due a service?")
        await add_turn(conn, "turn-t4", session_id="t-2", base=B, minutes_ago=20, question="Boilers at B-102?")
        await add_turn(conn, "turn-t5", session_id="t-3", base=B, minutes_ago=10, question="Lifts?")
    out = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="boiler"), cursor=None, limit=1)
    assert out["total"] == 2 and [c["key"] for c in out["conversations"]] == ["s:t-2"] and out["next_cursor"]
    nxt = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="boiler"), cursor=out["next_cursor"], limit=1)
    row = nxt["conversations"][0]
    assert row["key"] == "s:t-1" and row["turns_in_window"] == 3 and row["matching_turns"] == 2
    exact = await cv.list_conversations(ADMIN, ORG, cv.make_filter(q="turn-t5"), cursor=None, limit=5)
    assert exact["matched_turn_id"] == "turn-t5" and [c["key"] for c in exact["conversations"]] == ["s:t-3"]


async def test_updates_count_turns_written_after_the_watermark(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-u0", session_id="u-0", base=B, minutes_ago=5, created_minutes_ago=4)
    listed = await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=10)
    wm = datetime.fromisoformat(listed["watermark"])
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-u1", session_id="u-0", base=B, minutes_ago=20, created_minutes_ago=-1)
        await add_turn(conn, "turn-u2", session_id="u-2", base=B, minutes_ago=1, created_minutes_ago=-1)
        await add_turn(conn, "turn-u3", session_id="u-3", base=B, minutes_ago=120, created_minutes_ago=-1)  # past the look-back
    out = await cv.conversation_updates(ADMIN, ORG, cv.make_filter(), watermark=wm)
    assert (out["new_turns"], out["new_conversations"]) == (2, 2)


async def test_model_call_aggregate_counts_llm_and_router_spans_only(seeded):
    B = _now()
    root, group, llm, router, tool = (str(uuid.uuid4()) for _ in range(5))
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-m1", session_id="m-1", base=B)
        await add_span(conn, "turn-m1", root, None, 0, "turn", "turn", base=B)
        await add_span(conn, "turn-m1", group, root, 1, "agent", "compliance", base=B, agent="compliance", cost=0.5)
        await add_span(conn, "turn-m1", llm, group, 2, "llm", "analyst", base=B, agent="compliance", model="claude-sonnet-5",
                       cost=0.1, tin=1000, tout=100, inp={"system_prompt": "S", "user_message": "U"},
                       out={"model_output": "Use Bearer abcdefghijklmnop now"})
        await add_span(conn, "turn-m1", router, root, 3, "router", "router", base=B, agent="agent_router",
                       model="claude-haiku-4-5", cost=0.01, tin=50, tout=5)
        await add_span(conn, "turn-m1", tool, root, 4, "tool", "list_certificates", base=B, agent="compliance")
    out = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=100)
    agg = out["aggregate"]
    assert agg["calls"] == 2 and round(agg["cost_usd"], 6) == 0.11 and agg["input_tokens"] == 1050
    assert sorted(m["model"] for m in agg["by_model"]) == ["claude-haiku-4-5", "claude-sonnet-5"]
    assert {c["kind"] for c in out["calls"]} == {"llm", "router"}
    analyst = next(c for c in out["calls"] if c["kind"] == "llm")
    assert analyst["response_preview"] == "Use Bearer [redacted] now" and analyst["conversation_key"] == "s:m-1"
    assert analyst["request_kind"] == "exchange"
    assert next(c for c in out["calls"] if c["kind"] == "router")["label"] == "routing decision"
    routers = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True, kind="router"), cursor=None, limit=100)
    assert [c["kind"] for c in routers["calls"]] == ["router"]
    first = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=1)
    second = await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=first["next_cursor"], limit=1)
    assert first["next_cursor"] and second["next_cursor"] is None
    assert {first["calls"][0]["span_id"], second["calls"][0]["span_id"]} == {llm, router}


async def test_model_call_rows_are_redacted(seeded):
    B = _now()
    root, llm = str(uuid.uuid4()), str(uuid.uuid4())
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-x1", session_id="x-1", base=B, question="My key is sk-ant-api03-ABCDEFGHIJKLMNOPQRST, why?")
        await add_span(conn, "turn-x1", root, None, 0, "turn", "turn", base=B)
        await add_span(conn, "turn-x1", llm, root, 1, "llm", "model", base=B, model="claude-sonnet-5",
                       inp={"messages": 1, "last_human_message": "x", "tool_results_since_last_human": 0, "api_key": "k-1"},
                       out={"content": "token eyJhbGciOi.eyJzdWIiOiIx.SflKxwRJSM here", "tool_calls": []})
    row = (await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=10))["calls"][0]
    assert row["response_preview"] == "token [redacted token] here"
    assert "ABCDEFGHIJKLMNOPQRST" not in row["question_excerpt"]
    card = (await cv.get_model_call(ADMIN, ORG, llm))["model_call"]
    assert "k-1" not in json.dumps(card) and card["label"] == "answer synthesis"


async def test_deleted_threads_are_hidden_unless_asked(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_thread(conn, "d-1", "Old chat", deleted=True)
        await add_turn(conn, "turn-d1", session_id="d-1", base=B)
        await add_turn(conn, "turn-d2", session_id="d-2", base=B, minutes_ago=5)
    assert await keys_of() == ["s:d-2"]
    shown = await cv.list_conversations(ADMIN, ORG, cv.make_filter(deleted="include"), cursor=None, limit=10)
    row = next(c for c in shown["conversations"] if c["key"] == "s:d-1")
    assert "thread_deleted" in row["flags"] and row["session_label"] == "Old chat"
    assert await cv.get_conversation(ADMIN, ORG, "s:d-1") is None
    assert (await cv.get_conversation(ADMIN, ORG, "s:d-1", include_deleted=True))["thread_deleted"] is True


async def test_session_kind_and_group_key_sql_and_python_agree(seeded):
    B = _now()
    sessions = [None, "", "   ", "\t", "rerun", "rerun-x", "golden-1", "report-1", "docs-1", "ing-1", "skilllab-1",
                "thread-1", "other", "foreign-thread"]
    async with seeded.begin() as conn:
        await add_thread(conn, "thread-1", "A thread")
        await add_thread(conn, "golden-1", "Still synthetic")
        await add_thread(conn, "foreign-thread", "Another company's thread", org=OTHER)
        for i, sid in enumerate(sessions):
            await add_turn(conn, f"turn-k{i}", session_id=sid, base=B, minutes_ago=10 + i)
        rows = (await conn.execute(text(f"""
            SELECT tt.turn_id, tt.session_id, {cv.GROUP_KEY_SQL} AS k, {cv.SESSION_KIND_SQL} AS kind,
                   (th.id IS NOT NULL) AS th
              FROM {trace.TURNS} tt {cv.THREAD_JOIN}"""))).mappings().all()
    assert len(rows) == len(sessions)
    for r in rows:
        assert r["k"] == n.group_key(r["session_id"], r["turn_id"]), dict(r)
        assert r["kind"] == n.session_kind(r["session_id"], thread_exists=r["th"]), dict(r)
    kinds = {r["session_id"]: r["kind"] for r in rows}
    assert kinds["thread-1"] == "observed" and kinds["foreign-thread"] == "inferred"


async def test_inferred_channel_sql_and_classify_agree(seeded):
    from test_trace_lab_provenance import INFER

    B = _now()
    async with seeded.begin() as conn:
        for i, (question, session_id, route, answer, _, _) in enumerate(INFER):
            await add_turn(conn, f"turn-i{i:03d}", session_id=session_id, base=B, question=question, answer=answer,
                           route=route)
        rows = (await conn.execute(text(f"""
            SELECT turn_id, session_id, question, answer, route, {provenance.INFERRED_CHANNEL_SQL} AS channel
              FROM {trace.TURNS} tt ORDER BY turn_id"""))).mappings().all()
    assert len(rows) == len(INFER)
    for r, case in zip(rows, INFER):
        want = provenance.classify(r["question"], r["session_id"], r["route"], r["answer"]).channel
        assert r["channel"] == want == case[4], dict(r)


async def test_scope_never_reads_another_company(seeded):
    B = _now()
    root, llm = str(uuid.uuid4()), str(uuid.uuid4())
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-o1", session_id="o-1", base=B, org=OTHER)
        await add_span(conn, "turn-o1", root, None, 0, "turn", "turn", base=B, org=OTHER)
        await add_span(conn, "turn-o1", llm, root, 1, "llm", "analyst", base=B, model="claude-sonnet-5", org=OTHER)
        await add_thread(conn, "shared-id", "Other company's secret title", org=OTHER)
        await add_turn(conn, "turn-mine", session_id="shared-id", base=B, minutes_ago=3)
    assert await keys_of() == ["s:shared-id"]
    mine = (await cv.list_conversations(ADMIN, ORG, cv.make_filter(), cursor=None, limit=10))["conversations"][0]
    assert mine["session_label"] == "Session not in chat history" and mine["title"] is None
    assert await cv.get_conversation(ADMIN, ORG, "s:o-1") is None and await cv.get_turn(ADMIN, ORG, "turn-o1") is None
    assert (await cv.list_model_calls(ADMIN, ORG, cv.make_filter(calls=True), cursor=None, limit=10))["calls"] == []
    assert await cv.get_model_call(ADMIN, ORG, llm) is None
    facets = await cv.facets(ADMIN, ORG, cv.make_filter())
    assert facets["channels"] == [{"value": "chat", "turns": 1}]
    runs = await cv.list_runs(ADMIN, ORG, since=B - timedelta(days=1), until=B + timedelta(days=1))
    assert runs["runs"][0]["turn_count"] == 1


async def test_the_activity_row_repairs_the_question_and_refines_the_channel(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-l1", session_id="l-1", base=B, question="ctx" + n.QUESTION_MARKER + "Wrapped words")
        await add_activity(conn, "l-1", "turn-l1", {"message": "Typed words", "mode": "rest",
                                                    "context": "This is a Hoistra support session. Guide."})
    conv = await cv.get_conversation(ADMIN, ORG, "s:l-1")
    assert conv["active"]["input"] == "Typed words" and "question_repaired" in conv["active"]["flags"]
    assert conv["active"]["meta"]["channel"] == "support" and conv["active"]["meta"]["channel_source"] == "inferred"


async def test_runs_list_the_live_pseudo_run(seeded):
    B = _now()
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-r1", session_id="live-1", base=B, cost=0.02)
        await add_turn(conn, "turn-r2", session_id="live-1", base=B, minutes_ago=5, cost=0.03, models=("gpt-5.6-terra",))
    runs = await cv.list_runs(ADMIN, ORG, since=B - timedelta(days=1), until=B + timedelta(days=1))
    assert runs["golden_available"] is True and len(runs["runs"]) == 1
    live = runs["runs"][0]
    assert (live["run_id"], live["conversation_count"], live["turn_count"]) == ("live", 1, 2)
    assert round(live["cost_usd"], 6) == 0.05 and live["models"] == ["claude-sonnet-5", "gpt-5.6-terra"]


async def test_reads_are_read_only_even_on_a_writable_engine(seeded):
    async with seeded.begin() as conn:
        await add_turn(conn, "turn-w1", session_id="w-1", base=_now())
    with pytest.raises(DBAPIError) as e:
        async with cv._read() as conn:
            await conn.execute(text(f"DELETE FROM {trace.TURNS}"))
    assert "read-only" in str(e.value).lower()
    assert await keys_of() == ["s:w-1"]
