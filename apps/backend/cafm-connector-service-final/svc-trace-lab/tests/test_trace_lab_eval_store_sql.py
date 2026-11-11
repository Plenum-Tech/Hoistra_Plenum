"""The eval store on a throwaway Postgres (11 Nov 2026, task plan B1.1 and B1.2).

B1.1: every turn of a conversation is filed under one session, in turn order, so a multi-turn scenario opens as one
session with a trace per turn. B1.2: each trace keeps its whole tree of linked steps. Both have to hold when the same
conversation is read twice (nothing doubles), when a later turn arrives after an earlier read (it becomes the next
turn, not a lost one), and for a caller from another company (who must not see it).
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, T0, UID, member, sample_turn
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, labelling, trace_tree
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)
SID = "scn__baseline_v0__MT-001__1"


def _meta(session_id: str = SID, *, tag: str | None = "baseline_v0", org: str = ORG, attempt: int = 1,
          scenario: tuple[str, int] | None = None, at: datetime = T0) -> eval_store.SessionMeta:
    return eval_store.SessionMeta(
        id=session_id, organization_id=org, run_tag=tag, scenario_id=scenario[0] if scenario else None,
        scenario_version=scenario[1] if scenario else None, attempt=attempt, origin="test", source="runner",
        user_id=UID, email="asker@example.com", role="property_manager", space="ops", started_at=at)


async def _tag(lab, tag: str = "baseline_v0") -> None:
    async with lab.begin() as c:
        await c.execute(text("INSERT INTO tracelab.run_tags (tag, kind, origin) VALUES (:t, 'baseline', 'test')"
                             " ON CONFLICT DO NOTHING"), {"t": tag})


async def _scenario(lab, scenario_id: str = "MT-001", version: int = 1, kind: str = "multi_turn") -> None:
    async with lab.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.scenarios (scenario_id, version, type, spec_sections, turns, content_sha256, status,"
            " source) VALUES (:id, :v, :k, ARRAY['multi_turn_continuity'], CAST(:t AS jsonb), repeat('a', 64), 'approved',"
            " 'import')"), {"id": scenario_id, "v": version, "k": kind,
                            "t": json.dumps([{"message": "first", "expected": {"answer_format": "success"}},
                                             {"message": "second", "expected": {"answer_format": "success"}}])})


async def _write(lab, meta, turns) -> dict[str, int]:
    async with lab.begin() as c:
        return await eval_store.write_session(c, meta, [eval_store.rows_for(t, s) for t, s in turns])


async def _count(lab, table: str) -> int:
    async with lab.connect() as c:
        return (await c.execute(text(f"SELECT count(*) FROM tracelab.{table}"))).scalar_one()


async def _main_turn(main, turn, spans) -> None:
    """One turn and its spans in the chat's own tables, as svc-deepagents stores them."""
    async with main.begin() as c:
        await c.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens, output_tokens,
                 cache_read_tokens, models, tools, ok, error)
            VALUES (:turn_id, :session_id, CAST(:organization_id AS uuid), CAST(:user_id AS uuid), :email, :question,
                    :answer, :route, CAST(:started_at AS timestamptz), CAST(:ended_at AS timestamptz), :latency_ms,
                    :llm_calls, :tool_calls, :cost_usd, :cost_complete, :input_tokens, :output_tokens,
                    :cache_read_tokens, CAST(:models AS jsonb), CAST(:tools AS jsonb), :ok, :error)"""),
            {**turn, "started_at": datetime.fromisoformat(turn["started_at"]),
             "ended_at": datetime.fromisoformat(turn["ended_at"]),
             "models": json.dumps(turn["models"]), "tools": json.dumps(turn["tools"])})
        for s in spans:
            await c.execute(text("""
                INSERT INTO plenum_cafm.agent_trace_spans
                    (id, turn_id, session_id, organization_id, parent_id, seq, kind, name, agent, model, started_at,
                     ended_at, latency_ms, input, output, input_tokens, output_tokens, cache_read_tokens, cost_usd, ok,
                     error)
                VALUES (CAST(:id AS uuid), :turn_id, :session_id, CAST(:organization_id AS uuid),
                        CAST(:parent_id AS uuid), :seq, :kind, :name, :agent, :model,
                        CAST(:started_at AS timestamptz), CAST(:ended_at AS timestamptz), :latency_ms,
                        CAST(:input AS jsonb), CAST(:output AS jsonb), :input_tokens, :output_tokens,
                        :cache_read_tokens, :cost_usd, :ok, :error)"""),
                {**s, "started_at": datetime.fromisoformat(s["started_at"]),
                 "ended_at": datetime.fromisoformat(s["ended_at"]),
                 "input": json.dumps(s["input"]) if s["input"] is not None else None,
                 "output": json.dumps(s["output"]) if s["output"] is not None else None})


async def test_a_session_is_written_with_a_trace_per_turn_and_every_step(lab_scratch):
    await _tag(lab_scratch)
    t1, t2 = sample_turn("turn-1"), sample_turn("turn-2", at=T0 + timedelta(minutes=1))
    out = await _write(lab_scratch, _meta(), [t1, t2])
    assert out == {"sessions": 1, "traces": 2, "observations": 8}
    async with lab_scratch.connect() as c:
        rows = (await c.execute(text("SELECT id, turn_index FROM tracelab.traces ORDER BY turn_index"))).all()
    assert [tuple(r) for r in rows] == [("turn-1", 1), ("turn-2", 2)]


async def test_writing_the_same_session_twice_inserts_nothing_the_second_time(lab_scratch):
    await _tag(lab_scratch)
    turns = [sample_turn("turn-1")]
    await _write(lab_scratch, _meta(), turns)
    assert await _write(lab_scratch, _meta(), turns) == {"sessions": 0, "traces": 0, "observations": 0}
    assert (await _count(lab_scratch, "sessions"), await _count(lab_scratch, "traces"),
            await _count(lab_scratch, "observations")) == (1, 1, 4)


async def test_the_stored_tree_is_the_tree_the_trace_tab_draws(lab_scratch):
    await _tag(lab_scratch)
    turn, spans = sample_turn("turn-1")
    await _write(lab_scratch, _meta(), [(turn, spans)])
    got = await eval_store.trace_tree_of(member(), "turn-1")
    want = trace_tree.build_tree(turn, spans)
    assert got["nodes"] == want["nodes"]
    assert got["turn"] == want["turn"] and got["purged"] is False


async def test_a_nul_character_in_a_tool_output_does_not_lose_the_session(lab_scratch):
    await _tag(lab_scratch)
    turn, spans = sample_turn("turn-1")
    spans[2]["output"] = {"text": "bad\u0000byte"}
    assert (await _write(lab_scratch, _meta(), [(turn, spans)]))["observations"] == 4


async def test_backfill_groups_the_turns_of_a_scenario_conversation_into_one_session(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-b", SID, at=T0 + timedelta(minutes=2)))
    await _main_turn(main_scratch, *sample_turn("turn-a", SID, at=T0))
    await _main_turn(main_scratch, *sample_turn("turn-chat", "an-ordinary-chat-session", at=T0))
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["traces"], out["observations"]) == (1, 2, 8)
    async with lab_scratch.connect() as c:
        s = (await c.execute(text("SELECT id, run_tag, scenario_id, scenario_version, attempt, source,"
                                  " organization_id::text AS org FROM tracelab.sessions"))).mappings().one()
        order = [r[0] for r in (await c.execute(text("SELECT id FROM tracelab.traces ORDER BY turn_index"))).all()]
        kind = (await c.execute(text("SELECT kind FROM tracelab.run_tags WHERE tag = 'baseline_v0'"))).scalar_one()
    assert dict(s) == {"id": SID, "run_tag": "baseline_v0", "scenario_id": "MT-001", "scenario_version": 1,
                       "attempt": 1, "source": "chat", "org": ORG}
    assert order == ["turn-a", "turn-b"] and kind == "baseline"


async def test_backfill_twice_adds_nothing(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-a", SID))
    first = await eval_store.ingest_from_main(since=EPOCH)
    assert (first["sessions"], first["traces"], first["observations"]) == (1, 1, 4)
    again = await eval_store.ingest_from_main(since=EPOCH)
    assert (again["sessions"], again["traces"], again["observations"]) == (0, 0, 0)


async def test_a_turn_that_arrives_after_an_earlier_backfill_becomes_the_next_turn(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-a", SID, at=T0))
    await eval_store.ingest_from_main(since=EPOCH)
    await _main_turn(main_scratch, *sample_turn("turn-b", SID, at=T0 + timedelta(minutes=5)))
    out = await eval_store.ingest_from_main(since=T0 + timedelta(minutes=1))     # only the new turn is "since"
    assert (out["sessions"], out["traces"]) == (0, 1)
    async with lab_scratch.connect() as c:
        rows = (await c.execute(text("SELECT id, turn_index FROM tracelab.traces ORDER BY turn_index"))).all()
    assert [tuple(r) for r in rows] == [("turn-a", 1), ("turn-b", 2)]


async def test_a_conversation_whose_scenario_is_not_stored_yet_waits_and_is_filed_once_it_is(lab_scratch, main_scratch):
    """Review finding 3: a session row can never be changed, so one filed before its scenario existed would stay
    unlinked for ever. It waits instead, and the next read files it."""
    await _main_turn(main_scratch, *sample_turn("turn-a", "scn__baseline_v0__NOT-YET__1"))
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["waiting"]) == (0, 1) and await _count(lab_scratch, "sessions") == 0
    await _scenario(lab_scratch, "NOT-YET", kind="single_domain")
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["waiting"]) == (1, 0)
    async with lab_scratch.connect() as c:
        assert (await c.execute(text("SELECT scenario_id FROM tracelab.sessions"))).scalar_one() == "NOT-YET"


async def test_two_companies_using_the_same_session_id_never_share_a_session(lab_scratch, main_scratch):
    """Review finding 1: the id is deterministic, so two companies can both ask scn__baseline_v0__MT-001__1."""
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-ours", SID, org=ORG, at=T0))
    await _main_turn(main_scratch, *sample_turn("turn-theirs", SID, org=OTHER_ORG, at=T0 + timedelta(minutes=1)))
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["traces"], out["other_company"]) == (1, 1, 1)
    async with lab_scratch.connect() as c:
        rows = (await c.execute(text("SELECT id, organization_id::text FROM tracelab.traces"))).all()
    assert len(rows) == 1, "one company's turn only: the other's is never filed under it"
    owner = rows[0][1]
    outsider = member(org=OTHER_ORG if owner == ORG else ORG, role="admin", all_companies=False)
    with pytest.raises(NotFound):
        await eval_store.get_session(outsider, SID)
    again = await eval_store.ingest_from_main(since=EPOCH, org=OTHER_ORG if owner == ORG else ORG)
    assert (again["sessions"], again["traces"], again["other_company"]) == (0, 0, 1)
    assert await _count(lab_scratch, "traces") == 1


async def test_every_conversation_is_read_however_many_there_are(lab_scratch, main_scratch, monkeypatch):
    """Review finding 4: reading stopped at a fixed number of sessions with no way to reach the rest."""
    monkeypatch.setattr(eval_store, "INGEST_PAGE", 2)
    for i in range(5):
        await _scenario(lab_scratch, f"SD-{i:03d}", kind="single_domain")
        await _main_turn(main_scratch, *sample_turn(f"turn-{i}", f"scn__baseline_v0__SD-{i:03d}__1"))
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["traces"]) == (5, 5)
    assert (await eval_store.ingest_from_main(since=EPOCH))["sessions"] == 0


async def test_a_turn_recorded_late_with_an_earlier_time_is_added_as_the_next_turn_not_lost(lab_scratch, main_scratch):
    """Review finding 5: the position in the re-read list collided with a stored turn's number; the turn vanished
    and the read failed."""
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-b", SID, at=T0 + timedelta(minutes=5)))
    await eval_store.ingest_from_main(since=EPOCH)
    await _main_turn(main_scratch, *sample_turn("turn-a", SID, at=T0))            # written late, asked earlier
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["traces"], out["failed"]) == (1, 0)
    async with lab_scratch.connect() as c:
        rows = (await c.execute(text("SELECT id, turn_index FROM tracelab.traces ORDER BY turn_index"))).all()
        spans = (await c.execute(text("SELECT count(*) FROM tracelab.observations"))).scalar_one()
    assert [tuple(r) for r in rows] == [("turn-b", 1), ("turn-a", 2)] and spans == 8


async def test_one_conversation_that_cannot_be_filed_does_not_stop_the_others(lab_scratch, main_scratch, monkeypatch):
    for sid in ("SD-001", "SD-002"):
        await _scenario(lab_scratch, sid, kind="single_domain")
        await _main_turn(main_scratch, *sample_turn("turn-" + sid, f"scn__baseline_v0__{sid}__1"))
    real = eval_store.write_session

    async def breaks_once(conn, meta, traces):
        if meta.scenario_id == "SD-001":
            raise RuntimeError("boom")
        return await real(conn, meta, traces)

    monkeypatch.setattr(eval_store, "write_session", breaks_once)
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["failed"]) == (1, 1)


async def test_a_session_is_linked_only_to_a_scenario_its_company_may_see(lab_scratch, main_scratch):
    """Review finding 2: another company's version of a scenario id must never be the one a session shows."""
    async with lab_scratch.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.scenarios (scenario_id, version, organization_id, type, spec_sections, turns,"
            " content_sha256, status, source) VALUES ('SD-001', 1, CAST(:o AS uuid), 'single_domain',"
            " ARRAY['output_format'], CAST(:t AS jsonb), repeat('a', 64), 'approved', 'import')"),
            {"o": OTHER_ORG, "t": json.dumps([{"message": "theirs", "expected": {"answer_format": "success"}}])})
    await _main_turn(main_scratch, *sample_turn("turn-a", "scn__baseline_v0__SD-001__1", org=ORG))
    out = await eval_store.ingest_from_main(since=EPOCH)
    assert (out["sessions"], out["waiting"]) == (0, 1), "the only version belongs to another company"


async def test_named_chat_sessions_are_filed_under_the_run_tag_given(lab_scratch, main_scratch):
    await _scenario(lab_scratch, "SD-7-001", kind="single_domain")
    await _main_turn(main_scratch, *sample_turn("turn-x", "chat-session-41"))
    await _main_turn(main_scratch, *sample_turn("turn-y", "chat-session-42"))
    out = await eval_store.ingest_from_main(since=EPOCH, run_tag="baseline_v0",
                                            sessions={"chat-session-41": "SD-7-001"})
    assert out["sessions"] == 1
    async with lab_scratch.connect() as c:
        s = (await c.execute(text("SELECT id, run_tag, scenario_id FROM tracelab.sessions"))).mappings().one()
    assert dict(s) == {"id": "chat-session-41", "run_tag": "baseline_v0", "scenario_id": "SD-7-001"}
    free = await eval_store.ingest_from_main(since=EPOCH, run_tag="baseline_v0", sessions={"chat-session-42": None})
    assert free["sessions"] == 1, "named with no scenario: filed, and shown as not linked to one"


async def test_one_company_reads_only_its_own_turns_into_the_store(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _scenario(lab_scratch, "MT-002")
    await _main_turn(main_scratch, *sample_turn("turn-a", SID, org=ORG))
    await _main_turn(main_scratch, *sample_turn("turn-o", "scn__baseline_v0__MT-002__1", org=OTHER_ORG))
    out = await eval_store.ingest_from_main(since=EPOCH, org=ORG)
    assert out["sessions"] == 1 and await _count(lab_scratch, "sessions") == 1


async def test_the_list_shows_one_row_per_session_with_its_turns_type_and_status(lab_scratch):
    await _tag(lab_scratch)
    await _scenario(lab_scratch)
    ok_turn, ok_spans = sample_turn("turn-ok")
    for s in ok_spans:
        s["ok"], s["error"] = True, None
    await _write(lab_scratch, _meta(scenario=("MT-001", 1)), [sample_turn("turn-1"), sample_turn("turn-2")])
    await _write(lab_scratch, _meta("scn__baseline_v0__SD-001__1", at=T0 + timedelta(hours=1)), [(ok_turn, ok_spans)])
    out = await eval_store.list_sessions(member())
    rows = {r["id"]: r for r in out["sessions"]}
    assert [r["id"] for r in out["sessions"]] == ["scn__baseline_v0__SD-001__1", SID]          # newest first
    assert rows[SID]["turns"] == 2 and rows[SID]["type"] == "multi_turn" and rows[SID]["scenario_id"] == "MT-001"
    assert rows[SID]["status"] == "failed"              # a tool call in it failed
    assert rows["scn__baseline_v0__SD-001__1"]["status"] == "ok"
    assert rows[SID]["run_tag"] == "baseline_v0" and rows[SID]["origin"] == "test"


async def test_the_list_filters_by_run_tag_type_and_failure_status(lab_scratch):
    await _tag(lab_scratch)
    await _tag(lab_scratch, "postfix_v1")
    await _scenario(lab_scratch)
    await _write(lab_scratch, _meta(scenario=("MT-001", 1)), [sample_turn("turn-1")])
    await _write(lab_scratch, _meta("scn__postfix_v1__MT-001__1", tag="postfix_v1", scenario=("MT-001", 1)),
                 [sample_turn("turn-2")])
    ids = lambda out: [r["id"] for r in out["sessions"]]                                    # noqa: E731
    assert ids(await eval_store.list_sessions(member(), run_tag="postfix_v1")) == ["scn__postfix_v1__MT-001__1"]
    assert len(ids(await eval_store.list_sessions(member(), type="multi_turn"))) == 2
    assert ids(await eval_store.list_sessions(member(), type="failure")) == []
    assert len(ids(await eval_store.list_sessions(member(), status="failed"))) == 2
    assert ids(await eval_store.list_sessions(member(), status="ok")) == []


async def test_the_list_shows_only_the_latest_attempt_unless_asked_for_all(lab_scratch):
    await _tag(lab_scratch)
    await _scenario(lab_scratch)
    await _write(lab_scratch, _meta(scenario=("MT-001", 1)), [sample_turn("turn-1")])
    await _write(lab_scratch, _meta("scn__baseline_v0__MT-001__2", attempt=2, scenario=("MT-001", 1)),
                 [sample_turn("turn-2")])
    latest = await eval_store.list_sessions(member())
    assert [r["id"] for r in latest["sessions"]] == ["scn__baseline_v0__MT-001__2"]
    every = await eval_store.list_sessions(member(), all_attempts=True)
    assert len(every["sessions"]) == 2


async def test_a_session_opens_with_its_scenario_and_its_traces_in_turn_order(lab_scratch):
    await _tag(lab_scratch)
    await _scenario(lab_scratch)
    await _write(lab_scratch, _meta(scenario=("MT-001", 1)),
                 [sample_turn("turn-1"), sample_turn("turn-2", at=T0 + timedelta(minutes=1))])
    got = await eval_store.get_session(member(), SID)
    assert got["session"]["id"] == SID and got["session"]["run_tag"] == "baseline_v0"
    assert [t["turn_index"] for t in got["traces"]] == [1, 2] and [t["id"] for t in got["traces"]] == ["turn-1", "turn-2"]
    assert got["scenario"]["scenario_id"] == "MT-001" and got["scenario"]["version"] == 1
    assert [t["message"] for t in got["scenario"]["turns"]] == ["first", "second"]
    assert got["traces"][0]["input"].startswith("Which certificates") and got["traces"][0]["status"] == "ok"


async def test_another_company_cannot_list_or_open_the_session(lab_scratch):
    await _tag(lab_scratch)
    await _write(lab_scratch, _meta(), [sample_turn("turn-1")])
    outsider = member(org=OTHER_ORG, role="admin", all_companies=False)
    assert (await eval_store.list_sessions(outsider))["sessions"] == []
    with pytest.raises(NotFound):
        await eval_store.get_session(outsider, SID)
    with pytest.raises(NotFound):
        await eval_store.trace_tree_of(outsider, "turn-1")
    insider = member(org=ORG, role="admin", all_companies=False)
    assert [r["id"] for r in (await eval_store.list_sessions(insider))["sessions"]] == [SID]


async def test_run_tags_are_listed_with_how_many_sessions_they_hold(lab_scratch):
    await eval_store.create_run_tag(member(), "baseline_v0", kind="baseline", note="before the fixes")
    await _write(lab_scratch, _meta(), [sample_turn("turn-1")])
    tags = await eval_store.list_run_tags(member())
    assert [(t["tag"], t["kind"], t["sessions"]) for t in tags] == [("baseline_v0", "baseline", 1)]
    await eval_store.create_run_tag(member(), "baseline_v0", kind="baseline")          # asking again is not an error
    assert len(await eval_store.list_run_tags(member())) == 1


# ── notes on a trace's spans, written from the session page (11 Nov 2026) ─────────────────────────────

async def test_a_traces_chat_record_and_everyones_notes_on_it_are_read_for_the_session_page(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-a", SID))
    await eval_store.ingest_from_main(since=EPOCH)
    got = await eval_store.trace_notes(member(), "turn-a")
    assert (got["trace_id"], got["organization_id"], got["labels"]) == ("turn-a", ORG, [])
    assert got["record"]["id"] == "turn-a" and got["me"] == member().user_id
    asked = next(x for x in got["record"]["messages"] if x["role"] == "user")
    await labelling.save_label(labelling.for_org(member(), ORG), "turn-a", step_key=str(asked["id"]), label=None,
                               note="Asked about B-301, answered about B-302.", step_title="Input · chat")
    again = await eval_store.trace_notes(member(), "turn-a")
    assert [(n["note"], n["step_key"]) for n in again["labels"]] == [("Asked about B-301, answered about B-302.",
                                                                       str(asked["id"]))]


async def test_notes_on_a_trace_are_only_read_by_those_who_may_see_it(lab_scratch, main_scratch):
    await _scenario(lab_scratch)
    await _main_turn(main_scratch, *sample_turn("turn-a", SID))
    await eval_store.ingest_from_main(since=EPOCH)
    with pytest.raises(NotFound):
        await eval_store.trace_notes(member(org=OTHER_ORG, all_companies=False, role="admin"), "turn-a")
    with pytest.raises(NotFound):
        await eval_store.trace_notes(member(), "no-such-trace")


async def test_a_trace_whose_chat_record_is_gone_still_shows_its_notes_place_as_empty(lab_scratch, main_scratch):
    await _tag(lab_scratch)
    await _write(lab_scratch, _meta(), [sample_turn("turn-x", SID)])
    got = await eval_store.trace_notes(member(), "turn-x")
    assert got["record"] is None and got["labels"] == []
