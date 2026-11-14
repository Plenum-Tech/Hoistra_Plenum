"""A session copied here from another Trace Lab shows in Runs too (13 Nov 2026), on a throwaway Postgres.

Thirty sessions tagged frontend_nov13_202718 were asked on another machine and pushed into this lab. Sessions listed
them; Runs, which is built from this environment's chat, had nothing to build from - "the same is not reflecting in
run tab". Each turn of such a session now becomes a question in a run named after its run tag, and the question's
answer is read from the stored trace. A turn the chat here recorded stays with the chat's own sync.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, T0, member, sample_turn, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import runs, trace_tree

pytestmark = requires_scratch

PUSHED = "scn__frontend_nov13__frontend-pilot-001__1"
PUSHED_2 = "scn__frontend_nov13__frontend-pilot-002__1"


async def _rows(lab, sql: str) -> list[tuple]:
    async with lab.connect() as c:
        return [tuple(r) for r in (await c.execute(text(sql))).all()]


async def test_each_turn_of_a_copied_session_becomes_a_question_in_a_run_named_for_its_tag(lab_scratch, main_scratch):
    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1", "turn-p2"))
    await seed_session(lab_scratch, PUSHED_2, tag="frontend_nov13", turn_ids=("turn-p3",), at=T0 + timedelta(minutes=10))
    out = await runs.sync_sessions(None)
    assert out == {"new_questions": 3, "new_runs": 1, "more": False}
    run = await _rows(lab_scratch, "SELECT kind, title, source, organization_id::text FROM tracelab.lab_runs")
    # One batch of the tag (14 Nov 2026): its title names when the batch began.
    assert run == [("synthetic", "Sessions · frontend_nov13 · 11 Nov 09:00", "sync", ORG)]
    asked = await _rows(lab_scratch, "SELECT q.seq_no, l.turn_id, q.source, l.source FROM tracelab.lab_questions q"
                                     " JOIN tracelab.lab_question_traces l ON l.question_id = q.id ORDER BY q.seq_no")
    assert asked == [(1, "turn-p1", "sync", "sync"), (2, "turn-p2", "sync", "sync"), (3, "turn-p3", "sync", "sync")]
    q = await _rows(lab_scratch, "SELECT question FROM tracelab.lab_questions WHERE seq_no = 1")
    assert q == [("Which certificates expire in the next 30 days at B-301?",)]


async def test_syncing_again_adds_nothing_and_a_later_session_joins_the_same_run(lab_scratch, main_scratch):
    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1",))
    assert (await runs.sync_sessions(None))["new_questions"] == 1
    assert await runs.sync_sessions(None) == {"new_questions": 0, "new_runs": 0, "more": False}
    await seed_session(lab_scratch, PUSHED_2, tag="frontend_nov13", turn_ids=("turn-p3",), at=T0 + timedelta(minutes=10))
    assert await runs.sync_sessions(None) == {"new_questions": 1, "new_runs": 0, "more": False}
    assert await _rows(lab_scratch, "SELECT count(*) FROM tracelab.lab_runs") == [(1,)]
    assert await _rows(lab_scratch, "SELECT max(seq_no) FROM tracelab.lab_questions") == [(2,)]


async def test_another_run_tag_and_another_company_each_get_their_own_run(lab_scratch, main_scratch):
    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1",))
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", tag="baseline_v0", turn_ids=("turn-b1",))
    await seed_session(lab_scratch, "scn__frontend_nov13__frontend-pilot-009__1", tag="frontend_nov13",
                       turn_ids=("turn-o1",), org=OTHER_ORG)
    assert (await runs.sync_sessions(None))["new_runs"] == 3
    got = await _rows(lab_scratch, "SELECT title, organization_id::text FROM tracelab.lab_runs ORDER BY title, 2")
    assert sorted(got) == sorted([("Sessions · baseline_v0 · 11 Nov 09:00", ORG), ("Sessions · frontend_nov13 · 11 Nov 09:00", ORG),
                                  ("Sessions · frontend_nov13 · 11 Nov 09:00", OTHER_ORG)])
    only = await runs.sync_sessions(OTHER_ORG)
    assert only == {"new_questions": 0, "new_runs": 0, "more": False}


async def test_a_chat_turn_just_recorded_here_is_left_to_the_chats_own_sync(lab_scratch, main_scratch):
    """It becomes a question of that person's day; listing it under its run tag as well would file it twice."""
    from test_trace_lab_eval_store_sql import _main_turn

    now = datetime.now(timezone.utc)
    chat = "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70"
    turn, spans = sample_turn("turn-live", chat, at=now - timedelta(minutes=5))
    await _main_turn(main_scratch, turn, spans)
    await seed_session(lab_scratch, chat, tag="chat", turn_ids=("turn-live",), at=now - timedelta(minutes=5))
    assert await runs.sync_sessions(None, now=now) == {"new_questions": 0, "new_runs": 0, "more": False}
    live = await runs.sync_live(None, now=now)
    assert live["new_questions"] == 1
    assert await runs.sync_sessions(None, now=now) == {"new_questions": 0, "new_runs": 0, "more": False}
    assert await _rows(lab_scratch, "SELECT kind FROM tracelab.lab_runs") == [("production",)]


async def test_a_scenario_turn_recorded_here_goes_to_its_run_tags_run_at_once_never_a_persons_day(lab_scratch, main_scratch):
    """Hussain, 13 Nov 2026: live runs keep only what people ask by hand. A scenario session's turn (session id
    scn__<tag>__<scenario>__<n>) is never the chat's own sync's, so there is nothing to wait an hour for."""
    from test_trace_lab_eval_store_sql import _main_turn

    now = datetime.now(timezone.utc)
    turn, spans = sample_turn("turn-scn", "scn__baseline_v0__MT-001__1", at=now - timedelta(minutes=5))
    await _main_turn(main_scratch, turn, spans)
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", tag="baseline_v0", turn_ids=("turn-scn",),
                       at=now - timedelta(minutes=5))
    assert (await runs.sync_live(None, now=now))["new_questions"] == 0, "not a question of the asker's day"
    assert await runs.sync_sessions(None, now=now) == {"new_questions": 1, "new_runs": 1, "more": False}
    assert await _rows(lab_scratch, "SELECT kind, title, source FROM tracelab.lab_runs") == [
        ("synthetic", runs.batch_title("baseline_v0", now - timedelta(minutes=5)), "sync")]
    assert await runs.sync_sessions(None, now=now) == {"new_questions": 0, "new_runs": 0, "more": False}


async def test_the_runs_list_can_show_only_the_runs_made_from_sessions(lab_scratch, main_scratch):
    """The Runs page's Sessions filter (13 Nov 2026): a run made from a run tag's sessions is synthetic and synced."""
    import pytest

    from trace_lab.review_errors import Invalid

    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1",))
    await runs.sync_sessions(None)
    batch = await runs.create_run(member(), title="A typed batch", questions=[{"question": "How many floors?"}])
    listed = await runs.list_runs(member(), kind="sessions")
    assert [(r["title"], r["kind"], r["source"]) for r in listed["runs"]] == [
        ("Sessions · frontend_nov13 · 11 Nov 09:00", "synthetic", "sync")]
    assert listed["total"] == 1
    assert {r["title"] for r in (await runs.list_runs(member(), kind="synthetic"))["runs"]} == {
        "Sessions · frontend_nov13 · 11 Nov 09:00", "A typed batch"}, "the synthetic filter is unchanged"
    assert batch and (await runs.list_runs(member(), kind="production"))["runs"] == []
    with pytest.raises(Invalid):
        await runs.list_runs(member(), kind="everything")


async def test_the_question_of_a_copied_turn_opens_on_its_stored_trace(lab_scratch, main_scratch):
    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1", "turn-p2"))
    await runs.sync_sessions(None)
    m = member()
    listed = await runs.list_runs(m)
    code = next(r["code"] for r in listed["runs"] if r["run_tag"] == "frontend_nov13")
    questions = (await runs.list_questions(m, code))["questions"]
    assert [q["turn_ids"] for q in questions] == [["turn-p1"], ["turn-p2"]]
    got = await runs.get_question(m, questions[1]["code"])
    active = got["active"]
    assert active["turn_id"] == "turn-p2" and "missing" not in active and active["trace"] is None
    assert active["stored"] == {"session_id": PUSHED, "turn_index": 2, "run_tag": "frontend_nov13", "origin": "test"}
    tree = await trace_tree.question_tree(m, questions[1]["code"])
    assert tree["missing"] is False and tree["stored"] is True
    assert tree["tree"]["turn"]["turn_id"] == "turn-p2" and len(tree["tree"]["nodes"]) == 4
    assert tree["tree"]["nodes"][0]["depth"] == 0


async def test_a_turn_that_is_nowhere_is_still_said_to_be_missing(lab_scratch, main_scratch):
    await seed_session(lab_scratch, PUSHED, tag="frontend_nov13", turn_ids=("turn-p1",))
    await runs.sync_sessions(None)
    assert await runs.stored_answer(ORG, "turn-nowhere") is None
    assert await runs.stored_answer(OTHER_ORG, "turn-p1") is None            # another company's turn is not read
    assert (await runs.stored_answer(ORG, "turn-p1"))["session_id"] == PUSHED
