"""A run's conversations (14 Nov 2026, Hussain), on a throwaway Postgres.

The Sessions tab is gone: a "Sessions · <tag>" run and a person's live day run open on the Runs page as a list with
one row per conversation, the same rows the Sessions list had, and a row opens the same session page. A live day
run's conversations are the chat conversations its questions belong to (filed as sessions under run tag "chat"); a
question whose conversation is not filed yet is a "waiting" row until a sync files it. A sessions run's conversations
are its run tag's sessions, latest attempt first as on the old list. Typed and imported runs keep their questions.
Everything is the run's own company only. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, T0, member, sample_turn, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, runs
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

CHAT_A = "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70"
CHAT_B = "7a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
NOW = T0 + timedelta(hours=1)


async def _main(main, turn_id: str, session_id: str, *, at=T0, question: str | None = None) -> None:
    from test_trace_lab_eval_store_sql import _main_turn

    kw = {"question": question} if question else {}
    turn, spans = sample_turn(turn_id, session_id, at=at, **kw)
    await _main_turn(main, turn, spans)


async def _live_run(main) -> str:
    """One person's day: two questions in conversation A, one in conversation B."""
    await _main(main, "turn-a1", CHAT_A)
    await _main(main, "turn-a2", CHAT_A, at=T0 + timedelta(minutes=2))
    await _main(main, "turn-b1", CHAT_B, at=T0 + timedelta(minutes=5), question="Which vendors are blocked?")
    out = await runs.sync_live(None, now=NOW)
    assert out["new_questions"] == 3 and out["new_runs"] == 1
    listed = await runs.list_runs(member(), kind="production")
    return listed["runs"][0]["code"]


async def test_a_live_run_lists_the_conversations_its_questions_belong_to(lab_scratch, main_scratch):
    code = await _live_run(main_scratch)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    out = await eval_store.run_sessions(member(), code)
    assert out["mode"] == "live" and out["run"]["code"] == code
    assert [s["id"] for s in out["sessions"]] == [CHAT_A]
    row = out["sessions"][0]
    assert row["run_tag"] == "chat" and row["turns"] == 2 and row["status"] in ("ok", "failed")
    assert set(row) >= {"id", "organization_id", "run_tag", "scenario_id", "attempt", "origin", "type", "started_at",
                        "turns", "status", "stale", "expected_flagged"}, "the same row as GET /eval/sessions"
    assert row["turn_ids"] == ["turn-a1", "turn-a2"] and len(row["codes"]) == 2


async def test_a_question_whose_conversation_is_not_filed_is_a_waiting_row(lab_scratch, main_scratch):
    code = await _live_run(main_scratch)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    out = await eval_store.run_sessions(member(), code)
    assert [(w["turn_id"], w["question"]) for w in out["waiting"]] == [("turn-b1", "Which vendors are blocked?")]
    w = out["waiting"][0]
    assert w["code"].startswith(code + "-") and w["asked_at"]
    # Once a sync files conversation B, it is an ordinary row.
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("turn-b1",), at=T0 + timedelta(minutes=5))
    again = await eval_store.run_sessions(member(), code)
    assert again["waiting"] == [] and {s["id"] for s in again["sessions"]} == {CHAT_A, CHAT_B}


async def test_a_conversation_counts_only_for_the_runs_own_turns(lab_scratch, main_scratch):
    """A conversation that goes on the next day is a row of both days; each day names only its own questions."""
    code = await _live_run(main_scratch)
    await _main(main_scratch, "turn-a3", CHAT_A, at=T0 + timedelta(days=1))
    await runs.sync_live(None, now=NOW + timedelta(days=1))
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2", "turn-a3"))
    day1 = await eval_store.run_sessions(member(), code)
    assert day1["sessions"][0]["turn_ids"] == ["turn-a1", "turn-a2"]
    codes = [r["code"] for r in (await runs.list_runs(member(), kind="production"))["runs"]]
    day2 = await eval_store.run_sessions(member(), next(c for c in codes if c != code))
    assert [s["id"] for s in day2["sessions"]] == [CHAT_A] and day2["sessions"][0]["turn_ids"] == ["turn-a3"]


async def test_another_companys_run_and_conversations_never_appear(lab_scratch, main_scratch):
    code = await _live_run(main_scratch)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    with pytest.raises(NotFound):
        await eval_store.run_sessions(member(org=OTHER_ORG, all_companies=False, role="admin"), code)
    own = await eval_store.run_sessions(member(org=ORG, all_companies=False, role="admin"), code)
    assert [s["id"] for s in own["sessions"]] == [CHAT_A]


async def test_a_sessions_run_lists_its_run_tags_sessions_of_its_own_company(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__frontend_nov13__p-001__1", tag="frontend_nov13", turn_ids=("turn-p1",))
    await seed_session(lab_scratch, "scn__frontend_nov13__p-002__1", tag="frontend_nov13", turn_ids=("turn-p2",),
                       at=T0 + timedelta(minutes=10))
    await seed_session(lab_scratch, "scn__frontend_nov13__p-009__1", tag="frontend_nov13", turn_ids=("turn-o1",),
                       org=OTHER_ORG)
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", tag="baseline_v0", turn_ids=("turn-b1",))
    await runs.sync_sessions(None)
    listed = (await runs.list_runs(member(), kind="sessions", company=ORG))["runs"]
    code = next(r["code"] for r in listed if r["run_tag"] == "frontend_nov13")
    out = await eval_store.run_sessions(member(), code)
    assert out["mode"] == "sessions" and out["waiting"] == []
    assert [s["id"] for s in out["sessions"]] == ["scn__frontend_nov13__p-002__1", "scn__frontend_nov13__p-001__1"]
    assert out["sessions"][0]["codes"] == [code + "-2"]


async def test_a_sessions_run_shows_the_latest_attempt_unless_every_attempt_is_asked_for(lab_scratch, main_scratch):
    from test_trace_lab_eval_store_sql import _scenario

    await _scenario(lab_scratch)
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__1", turn_ids=("t-1",), scenario=("MT-001", 1))
    await seed_session(lab_scratch, "scn__baseline_v0__MT-001__2", turn_ids=("t-2",), scenario=("MT-001", 1), attempt=2,
                       at=T0 + timedelta(minutes=30))
    await runs.sync_sessions(None)
    code = (await runs.list_runs(member(), kind="sessions"))["runs"][0]["code"]
    latest = await eval_store.run_sessions(member(), code)
    assert [s["id"] for s in latest["sessions"]] == ["scn__baseline_v0__MT-001__2"]
    every = await eval_store.run_sessions(member(), code, all_attempts=True)
    assert len(every["sessions"]) == 2


async def test_a_typed_run_keeps_its_questions(lab_scratch, main_scratch):
    made = await runs.create_run(member(), title="A typed batch", company=ORG, questions=[{"question": "How many floors?"}])
    out = await eval_store.run_sessions(member(), made["code"])
    assert out["mode"] == "questions" and out["sessions"] == [] and out["waiting"] == []


async def test_sync_now_files_the_chat_conversations_too(lab_scratch, main_scratch):
    """Sync now makes questions of the chat's newer answers AND files their conversations as sessions, as "Read chat
    questions" did on the old Sessions tab, so a waiting row becomes a normal row with one press."""
    await _main(main_scratch, "turn-a1", CHAT_A)
    m = member(org=ORG, all_companies=False, role="admin")
    out = await runs.sync_production(m, now=NOW)
    assert out["new_questions"] == 1 and out["chat_sessions"] == 1
    code = (await runs.list_runs(m, kind="production"))["runs"][0]["code"]
    got = await eval_store.run_sessions(m, code)
    assert [s["id"] for s in got["sessions"]] == [CHAT_A] and got["waiting"] == []


async def test_the_route_returns_the_runs_conversations(lab_scratch, main_scratch):
    from trace_lab import routes_runs

    code = await _live_run(main_scratch)
    got = await routes_runs.lab_run_sessions(code, all_attempts=False, m=member())
    assert got["ok"] is True and got["mode"] == "live" and len(got["waiting"]) == 3
    json.dumps(got)


# ── 14 Nov 2026 (Hussain): "all the questions asked by codex agent should have a different tag not live" ─────────────
# Scenario sessions asked before 13 Nov were made questions of the asker's LIVE day, and a turn can be a synced question
# only once. A live day now shows and counts only the questions people asked by hand. Then: "each codex session is also
# a run, so it should also be R something": every scenario run tag gets a real run ("Sessions · <tag>", synthetic and
# synced, one per tag per company), made with no questions when its turns are already in live days; it opens on the
# tag's conversations and is counted from its sessions.

SCN = "scn__hussain_5q_v0__SD-1__1"
LATER = T0 + timedelta(hours=2)          # past the scenario grace: an unfiled scenario turn went to the asker's day


async def _old_day(main, lab, *, hand: bool = True, file_scenario: bool = True) -> str:
    """A live day made the old way: a scenario turn (asked by the Codex agent) and, optionally, a hand question."""
    await _main(main, "turn-s1", SCN, question="Which certificates lapse this month?")
    if hand:
        await _main(main, "turn-h1", CHAT_A, at=T0 + timedelta(minutes=3))
    out = await runs.sync_live(None, now=LATER)
    assert out["new_questions"] == (2 if hand else 1), "the scenario turn is in the live day, as before 13 Nov"
    if file_scenario:
        await seed_session(lab, SCN, tag="hussain_5q_v0", turn_ids=("turn-s1",))
    if hand:
        await seed_session(lab, CHAT_A, tag="chat", turn_ids=("turn-h1",), at=T0 + timedelta(minutes=3))
    return (await runs.list_runs(member(), kind="production"))["runs"][0]["code"]


def _tag_runs(listed):
    return [r for r in listed["runs"] if r["kind"] == "synthetic" and r["source"] == "sync"]


async def test_a_scenario_asked_before_the_change_is_not_in_the_live_run(lab_scratch, main_scratch):
    code = await _old_day(main_scratch, lab_scratch)
    live = next(r for r in (await runs.list_runs(member()))["runs"] if r["code"] == code)
    assert live["n_questions"] == 1 and live["n_traced"] == 1, "only the hand question counts"
    got = await eval_store.run_sessions(member(), code)
    assert [s["id"] for s in got["sessions"]] == [CHAT_A] and got["waiting"] == []
    # A link to the scenario question (Notes, Labels) still finds its conversation.
    assert [(o["session_id"], o["turn_id"]) for o in got["elsewhere"]] == [(SCN, "turn-s1")]


async def test_every_scenario_tag_is_a_real_run_even_when_its_turns_are_in_live_days(lab_scratch, main_scratch):
    await _old_day(main_scratch, lab_scratch)
    out = await runs.sync_sessions(None)
    assert out["new_questions"] == 0, "its turn is already a question of the live day"
    tagged = _tag_runs(await runs.list_runs(member()))
    assert [(r["run_tag"], r["title"], r["n_questions"], r["n_sessions"], r["n_turns"]) for r in tagged] == [
        ("hussain_5q_v0", "Sessions · hussain_5q_v0 · 11 Nov 09:00", 0, 1, 1)], "counted from its sessions; no run for chat"
    assert tagged[0]["code"].startswith("R") and tagged[0]["last_at"]
    await runs.sync_sessions(None)
    assert len(_tag_runs(await runs.list_runs(member()))) == 1, "never made twice"
    got = await eval_store.run_sessions(member(), tagged[0]["code"])
    assert got["mode"] == "sessions" and [s["id"] for s in got["sessions"]] == [SCN]


async def test_a_tag_run_is_made_per_company_and_stays_in_it(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__spec_v0__SD-1__1", tag="spec_v0", turn_ids=("turn-a",))
    await seed_session(lab_scratch, "scn__spec_v0__SD-2__1", tag="spec_v0", turn_ids=("turn-o",), org=OTHER_ORG)
    await runs.ensure_batch_runs(ORG)
    assert [r["organization_id"] for r in _tag_runs(await runs.list_runs(member()))] == [ORG]
    await runs.ensure_batch_runs(None)
    own = await runs.list_runs(member(org=ORG, all_companies=False, role="admin"))
    assert [(r["run_tag"], r["n_sessions"]) for r in _tag_runs(own)] == [("spec_v0", 1)]
    assert len(_tag_runs(await runs.list_runs(member()))) == 2


async def test_sync_now_and_read_new_sessions_make_the_tag_runs(lab_scratch, main_scratch):
    from trace_lab import routes_eval

    await seed_session(lab_scratch, "scn__spec_v0__SD-1__1", tag="spec_v0", turn_ids=("turn-a",))
    m = member(org=ORG, all_companies=False, role="admin")
    await runs.sync_production(m, now=NOW)
    assert [r["run_tag"] for r in _tag_runs(await runs.list_runs(m))] == ["spec_v0"]
    await seed_session(lab_scratch, "scn__spec_mt_v0__MT-1__1", tag="spec_mt_v0", turn_ids=("turn-b",))
    await routes_eval._ingest(m, routes_eval.IngestIn())
    assert sorted(r["run_tag"] for r in _tag_runs(await runs.list_runs(m))) == ["spec_mt_v0", "spec_v0"]


async def test_labels_and_progress_open_a_tag_run_with_no_questions(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__spec_v0__SD-1__1", tag="spec_v0", turn_ids=("turn-a",))
    await runs.ensure_batch_runs(None)
    code = _tag_runs(await runs.list_runs(member()))[0]["code"]
    assert (await runs.matrix(member(), code))["questions"] == [] if "questions" in (await runs.matrix(member(), code)) else True
    await runs.progress(member(), code)
    assert (await runs.list_questions(member(), code))["questions"] == []


async def test_the_runs_page_list_drops_scenario_only_live_days_and_sorts_by_activity(lab_scratch, main_scratch):
    from datetime import datetime, timezone

    day_only_scenarios = await _old_day(main_scratch, lab_scratch, hand=False)
    await seed_session(lab_scratch, "scn__spec_v0__SD-2__1", tag="spec_v0", turn_ids=("turn-x2",),
                       at=datetime.now(timezone.utc) + timedelta(minutes=5))
    await runs.ensure_batch_runs(None)
    page = await runs.list_runs(member(), for_page=True)
    tags = [r["run_tag"] for r in page["runs"]]
    assert day_only_scenarios not in [r["code"] for r in page["runs"]], "a live day of scenario questions only is left out"
    assert tags == ["spec_v0", "hussain_5q_v0"] and page["total"] == 2
    assert not any(r.get("entry") for r in page["runs"]), "no virtual tag entries any more"
    assert [r["run_tag"] for r in (await runs.list_runs(member(), for_page=True, kind="sessions"))["runs"]] == [
        "spec_v0", "hussain_5q_v0"]
    assert (await runs.list_runs(member(), for_page=True, q="spec_v0"))["total"] == 1
    assert day_only_scenarios in [r["code"] for r in (await runs.list_runs(member()))["runs"]], "the top bar is unchanged"


async def test_the_runs_route_passes_for_page(lab_scratch, main_scratch):
    from trace_lab import routes_runs

    await _old_day(main_scratch, lab_scratch, hand=False)
    got = await routes_runs.lab_runs(kind=None, q=None, company=None, limit=50, offset=0, for_page=True, m=member())
    assert got["runs"] == []


async def test_each_session_row_says_its_first_question_and_whether_it_was_asked_here(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__spec_v0__SD-1__1", tag="spec_v0", turn_ids=("turn-a", "turn-b"))
    await seed_session(lab_scratch, "scn__spec_v0__SD-2__1", tag="spec_v0", turn_ids=("turn-o",), org=OTHER_ORG)
    rows = (await eval_store.list_sessions(member(org=ORG, all_companies=False, role="admin")))["sessions"]
    assert [(r["id"], r["first_question"]) for r in rows] == [
        ("scn__spec_v0__SD-1__1", "Which certificates expire in the next 30 days at B-301?")]
    assert rows[0]["asked_here"] is (rows[0]["origin"] == eval_store.origin())


# ── 14 Nov 2026 (Hussain): runs in the order questions were last asked; a Codex run is one BATCH of a run tag ─────────
# "if aasim asked 50 questions through his codex that is one run and then again after one hour he asked 100 questions
# through codex that should be a different run". A tag's sessions (per company) split where one starts 60 minutes or
# more after the one before; each batch is a run "Sessions · <tag> · <start>". A run made earlier as "Sessions · <tag>"
# is the tag's first batch.

async def _legacy_run(lab, tag: str, org: str = ORG) -> str:
    async with lab.begin() as c:
        made = await runs._insert_run(c, None, kind="synthetic", title=f"Sessions · {tag}", org=org, source="sync")
    return runs.run_code(made["run_no"])


async def _batches_of(tag: str, m=None):
    listed = (await runs.list_runs(m or member(), kind="sessions"))["runs"]
    return sorted((r for r in listed if r.get("run_tag") == tag), key=lambda r: r["run_no"])


async def _ids(code: str, m=None, **kw):
    return [s["id"] for s in (await eval_store.run_sessions(m or member(), code, **kw))["sessions"]]


async def test_runs_are_ordered_by_the_latest_question_asked_across_kinds(lab_scratch, main_scratch):
    typed = await runs.create_run(member(), title="A typed batch", company=ORG, questions=[{"question": "How many floors?"}])
    await _main(main_scratch, "turn-h1", CHAT_A, at=T0 + timedelta(days=1))
    await runs.sync_live(None, now=T0 + timedelta(days=1, hours=2))
    live = (await runs.list_runs(member(), kind="production"))["runs"][0]["code"]
    await seed_session(lab_scratch, "scn__old_v0__SD-1__1", tag="old_v0", turn_ids=("t-o1",))   # asked at T0
    await runs.ensure_batch_runs(None)                         # made last, so made most recently
    batch = (await _batches_of("old_v0"))[0]["code"]
    want = [typed["code"], live, batch]                        # nothing asked (made now), the day after, T0
    assert [r["code"] for r in (await runs.list_runs(member()))["runs"]] == want, "the top-bar picker's order"
    assert [r["code"] for r in (await runs.list_runs(member(), for_page=True))["runs"]] == want, "the Runs page's order"


async def test_a_tag_asked_in_two_bursts_an_hour_apart_is_two_runs(lab_scratch, main_scratch):
    for sid, minutes in (("a", 0), ("b", 20), ("c", 80), ("d", 100)):      # c starts 60 minutes after b
        await seed_session(lab_scratch, f"scn__burst_v0__SD-{sid}__1", tag="burst_v0", turn_ids=(f"t-{sid}",),
                           at=T0 + timedelta(minutes=minutes))
    assert await runs.ensure_batch_runs(None) == 2
    assert await runs.ensure_batch_runs(None) == 0, "never made twice"
    one, two = await _batches_of("burst_v0")
    assert (one["title"], two["title"]) == ("Sessions · burst_v0 · 11 Nov 09:00", "Sessions · burst_v0 · 11 Nov 10:20")
    assert (one["n_sessions"], one["n_turns"], two["n_sessions"], two["n_turns"]) == (2, 2, 2, 2)
    assert await _ids(one["code"]) == ["scn__burst_v0__SD-b__1", "scn__burst_v0__SD-a__1"]
    assert await _ids(two["code"]) == ["scn__burst_v0__SD-d__1", "scn__burst_v0__SD-c__1"]


async def test_a_burst_under_an_hour_stays_one_run(lab_scratch, main_scratch):
    for sid, minutes in (("a", 0), ("b", 59), ("c", 118)):
        await seed_session(lab_scratch, f"scn__steady_v0__SD-{sid}__1", tag="steady_v0", turn_ids=(f"t-{sid}",),
                           at=T0 + timedelta(minutes=minutes))
    assert await runs.ensure_batch_runs(None) == 1
    (only,) = await _batches_of("steady_v0")
    assert only["n_sessions"] == 3 and len(await _ids(only["code"])) == 3


async def test_a_run_made_as_sessions_tag_is_the_tags_first_batch(lab_scratch, main_scratch):
    legacy = await _legacy_run(lab_scratch, "leg_v0")
    await seed_session(lab_scratch, "scn__leg_v0__SD-1__1", tag="leg_v0", turn_ids=("t-l1",))
    await seed_session(lab_scratch, "scn__leg_v0__SD-2__1", tag="leg_v0", turn_ids=("t-l2",), at=T0 + timedelta(hours=3))
    assert await runs.ensure_batch_runs(None) == 1, "no duplicate of the first batch"
    first, second = await _batches_of("leg_v0")
    assert first["code"] == legacy and first["title"] == "Sessions · leg_v0" and first["n_sessions"] == 1
    assert second["title"] == "Sessions · leg_v0 · 11 Nov 12:00" and second["n_sessions"] == 1
    assert await _ids(legacy) == ["scn__leg_v0__SD-1__1"]
    assert await _ids(second["code"]) == ["scn__leg_v0__SD-2__1"]


async def test_an_old_run_whose_questions_span_two_batches_hides_nothing(lab_scratch, main_scratch):
    """Bala's "Sessions · <tag>" runs hold questions for every turn of the tag: they stay its questions (Labels,
    notes); the later batch's conversations show in their own run, and a link to such a question still opens it."""
    legacy = await _legacy_run(lab_scratch, "frontend_nov13")
    await seed_session(lab_scratch, "scn__frontend_nov13__p-1__1", tag="frontend_nov13", turn_ids=("t-f1",))
    await seed_session(lab_scratch, "scn__frontend_nov13__p-2__1", tag="frontend_nov13", turn_ids=("t-f2",),
                       at=T0 + timedelta(hours=2))
    await runs._session_questions(None)                      # the old way: every turn a question of the one run
    assert len((await runs.list_questions(member(), legacy))["questions"]) == 2
    await runs.ensure_batch_runs(None)
    got = await eval_store.run_sessions(member(), legacy)
    assert [s["id"] for s in got["sessions"]] == ["scn__frontend_nov13__p-1__1"]
    assert [(o["session_id"], o["turn_id"]) for o in got["elsewhere"]] == [("scn__frontend_nov13__p-2__1", "t-f2")]
    assert len((await runs.list_questions(member(), legacy))["questions"]) == 2, "its questions are all still there"


async def test_a_third_burst_is_a_third_run_and_a_late_turn_stays_in_its_sessions_batch(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__three_v0__SD-1__1", tag="three_v0", turn_ids=("t-1",))
    await seed_session(lab_scratch, "scn__three_v0__SD-2__1", tag="three_v0", turn_ids=("t-2",), at=T0 + timedelta(hours=2))
    assert await runs.ensure_batch_runs(None) == 2
    await seed_session(lab_scratch, "scn__three_v0__SD-3__1", tag="three_v0", turn_ids=("t-3",), at=T0 + timedelta(hours=5))
    await seed_session(lab_scratch, "scn__three_v0__SD-1__1", tag="three_v0", turn_ids=("t-late",), at=T0 + timedelta(hours=6))
    assert await runs.ensure_batch_runs(None) == 1
    b1, b2, b3 = await _batches_of("three_v0")
    assert [b["n_sessions"] for b in (b1, b2, b3)] == [1, 1, 1]
    assert b1["n_turns"] == 2, "the late turn is a turn of its session, in its session's batch"
    assert await _ids(b3["code"]) == ["scn__three_v0__SD-3__1"]


async def test_batches_are_per_company(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__co_v0__SD-1__1", tag="co_v0", turn_ids=("t-1",))
    await seed_session(lab_scratch, "scn__co_v0__SD-9__1", tag="co_v0", turn_ids=("t-9",), org=OTHER_ORG,
                       at=T0 + timedelta(minutes=10))
    await seed_session(lab_scratch, "scn__co_v0__SD-8__1", tag="co_v0", turn_ids=("t-8",), org=OTHER_ORG,
                       at=T0 + timedelta(hours=3))
    assert await runs.ensure_batch_runs(None) == 3
    own = member(org=ORG, all_companies=False, role="admin")
    mine = await _batches_of("co_v0", own)
    assert [(r["organization_id"], r["n_sessions"]) for r in mine] == [(ORG, 1)]
    assert await _ids(mine[0]["code"], own) == ["scn__co_v0__SD-1__1"]
    theirs = await _batches_of("co_v0", member(org=OTHER_ORG, all_companies=False, role="admin"))
    assert [r["n_sessions"] for r in theirs] == [1, 1]


async def test_a_new_scenario_turn_becomes_a_question_of_its_batch(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__q_v0__SD-1__1", tag="q_v0", turn_ids=("t-1", "t-2"))
    await seed_session(lab_scratch, "scn__q_v0__SD-2__1", tag="q_v0", turn_ids=("t-3",), at=T0 + timedelta(hours=2))
    out = await runs.sync_sessions(None)
    assert out == {"new_questions": 3, "new_runs": 2, "more": False}
    one, two = await _batches_of("q_v0")
    assert [q["turn_ids"] for q in (await runs.list_questions(member(), one["code"]))["questions"]] == [["t-1"], ["t-2"]]
    assert [q["turn_ids"] for q in (await runs.list_questions(member(), two["code"]))["questions"]] == [["t-3"]]
    assert await runs.sync_sessions(None) == {"new_questions": 0, "new_runs": 0, "more": False}


# ── one Sync now for everything (14 Nov 2026, Hussain: "I only need one button which syncs everything together") ─────

async def _curator_asker(lab) -> None:
    from eval_fixtures import UID
    async with lab.begin() as c:
        await c.execute(text("INSERT INTO tracelab.lab_platform_members (user_id, email, lab_role, status, decided_by)"
                             " VALUES (CAST(:u AS uuid), 'asker@example.com', 'consultant', 'active', CAST(:u AS uuid))"),
                        {"u": UID})


async def test_sync_now_does_live_chat_codex_and_runs_in_one_call(lab_scratch, main_scratch):
    from datetime import datetime, timezone

    from test_trace_lab_eval_store_sql import _scenario

    now = datetime.now(timezone.utc)
    await _curator_asker(lab_scratch)
    await _scenario(lab_scratch, "SD-1")              # a scenario session is filed once its scenario is stored
    await _main(main_scratch, "turn-h1", CHAT_A, at=now - timedelta(minutes=20))
    await _main(main_scratch, "turn-s1", "scn__codex_v0__SD-1__1", at=now - timedelta(minutes=10))
    out = await runs.sync_production(member(org=ORG, all_companies=False, role="admin"), now=now)
    assert (out["new_questions"], out["chat_sessions"], out["codex_sessions"], out["failed"], out["skipped"]) == (
        2, 1, 1, {}, {}), out
    assert out["new_runs"] == 2, "the person's day and the Codex batch"
    (batch,) = await _batches_of("codex_v0")
    assert [q["turn_ids"] for q in (await runs.list_questions(member(), batch["code"]))["questions"]] == [["turn-s1"]]
    live = (await runs.list_runs(member(), kind="production"))["runs"]
    assert [r["n_questions"] for r in live] == [1], "the Codex question is not a live question"
    again = await runs.sync_production(member(org=ORG, all_companies=False, role="admin"), now=now)
    assert (again["new_questions"], again["chat_sessions"], again["codex_sessions"], again["new_runs"]) == (0, 0, 0, 0)


async def test_sync_now_skips_the_codex_read_for_someone_who_may_not_curate(lab_scratch, main_scratch):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    await _main(main_scratch, "turn-h1", CHAT_A, at=now - timedelta(minutes=20))
    await _main(main_scratch, "turn-s1", "scn__codex_v0__SD-1__1", at=now - timedelta(minutes=10))
    reader = member(org=ORG, all_companies=False, role="reviewer", manages=False)
    out = await runs.sync_production(reader, now=now)
    assert out["codex_sessions"] == 0 and "codex" in out["skipped"] and out["chat_sessions"] == 1
    assert out["new_questions"] == 1 and out["failed"] == {}


async def test_one_failed_step_never_stops_the_others(lab_scratch, main_scratch, monkeypatch):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    await _main(main_scratch, "turn-h1", CHAT_A, at=now - timedelta(minutes=20))
    await seed_session(lab_scratch, "scn__fail_v0__SD-1__1", tag="fail_v0", turn_ids=("t-1",))
    real = eval_store.ingest_from_main

    async def broken(**kw):
        if kw.get("chat"):
            raise RuntimeError("the chat database is asleep")
        return await real(**kw)

    monkeypatch.setattr(eval_store, "ingest_from_main", broken)
    out = await runs.sync_production(member(), now=now)
    assert out["failed"] == {"chat": "the chat database is asleep"}
    assert out["new_questions"] >= 1 and len(await _batches_of("fail_v0")) == 1, "the questions and the runs still synced"


async def test_the_automatic_sync_makes_the_batch_runs_too(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import autosync

    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_SESSION_RUNS", "0")
    await seed_session(lab_scratch, "scn__auto_v0__SD-1__1", tag="auto_v0", turn_ids=("t-1",))
    status = await autosync.tick()
    assert status["batch_runs_ok"] is True and status["last_new_batch_runs"] == 1
    assert len(await _batches_of("auto_v0")) == 1


# ── Codex sessions are read first (14 Nov 2026, Hussain): a scenario turn never becomes a live question when its
# session can be read in. Sync now and the automatic round both read Codex sessions and chat conversations, then make
# the batch runs and their questions, and only then sync the live questions.

async def _old_unfiled_scenario_turn(main, lab, now):
    """A Codex turn asked over an hour ago (past sync_live's grace), its session not read in yet, and a hand question."""
    from test_trace_lab_eval_store_sql import _scenario

    await _curator_asker(lab)
    await _scenario(lab, "SD-1")
    await _main(main, "turn-s1", "scn__late_v0__SD-1__1", at=now - timedelta(hours=2))
    await _main(main, "turn-h1", CHAT_A, at=now - timedelta(hours=2))


async def _where_is(lab, turn_id):
    async with lab.connect() as c:
        return [tuple(r) for r in (await c.execute(text(
            "SELECT r.kind, r.title FROM tracelab.lab_question_traces l JOIN tracelab.lab_questions q ON q.id = l.question_id"
            " JOIN tracelab.lab_runs r ON r.id = q.run_id WHERE l.turn_id = :t"), {"t": turn_id})).all()]


async def test_one_sync_now_puts_an_old_codex_turn_in_its_batch_never_a_live_day(lab_scratch, main_scratch):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    await _old_unfiled_scenario_turn(main_scratch, lab_scratch, now)
    out = await runs.sync_production(member(org=ORG, all_companies=False, role="admin"), now=now)
    assert out["failed"] == {} and out["codex_sessions"] == 1
    assert await _where_is(lab_scratch, "turn-s1") == [("synthetic", runs.batch_title("late_v0", now - timedelta(hours=2)))]
    assert await _where_is(lab_scratch, "turn-h1") == [("production", (await runs.list_runs(member(), kind="production"))["runs"][0]["title"])]


async def test_one_automatic_round_puts_an_old_codex_turn_in_its_batch_never_a_live_day(lab_scratch, main_scratch, monkeypatch):
    from datetime import datetime, timezone

    from trace_lab import autosync

    monkeypatch.setattr(autosync, "_scenario_read_at", None)
    now = datetime.now(timezone.utc)
    await _old_unfiled_scenario_turn(main_scratch, lab_scratch, now)
    status = await autosync.tick()
    assert status["last_new_scenario_sessions"] == 1 and status["last_ok"] is True
    assert await _where_is(lab_scratch, "turn-s1") == [("synthetic", runs.batch_title("late_v0", now - timedelta(hours=2)))]
    assert [k for k, _ in await _where_is(lab_scratch, "turn-h1")] == ["production"]
