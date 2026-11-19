"""One live run per company per day, shown as one run per day (19 Nov 2026, Hussain: "on 17 nov i asked question through
two accounts it should be stored in the same run as it was asked on the same date", then "it should stay as one run
only"), on a throwaway Postgres.

Storage: sync_live files every live answer of a company's UTC day in that company's one day run, whoever asked (the
run's title is "Live questions · <day>" and its owner is the nil uuid: no person, because a production run needs an
owner and the unique index (owner, company, day) then keeps it one). Listing: the per-person runs made before this
stay as they are (Trace Lab's runs are append-only) and are SHOWN merged: a company member's day is every run of their
company that day; the platform team's day is every company's runs that day, one entry, with who asked on each row.
Another company's rows never reach a company member. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, OTHER_ORG, member, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, runs
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

U_ANN, U_BOB, U_CAT = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
ANN, BOB, CAT = "ann@a.test", "bob@a.test", "cat@b.test"
DAY = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
NOW = DAY + timedelta(days=2)
CHAT_A = "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70"
CHAT_B = "7a1b2c3d-4e5f-4a6b-8c9d-0e1f2a3b4c5d"
CHAT_C = "9d8c7b6a-5f4e-4d3c-8b2a-1f0e9d8c7b6a"


async def _turn(main, tid, *, org, uid, email, at, question, session="s-1"):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, 'An answer.', 'single:wo_engine',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "sid": session, "org": org, "uid": uid, "email": email, "q": question, "at": at})


async def _old_run(lab, *, org, uid, email, day, asked):
    """A per-person day run as sync_live made them before 19 Nov 2026: owner = the person, one question per (turn, text, at)."""
    async with lab.begin() as conn:
        run = await runs._insert_run(conn, None, kind="production", title=f"{email} · {day}", org=org, source="sync",
                                     owner_user_id=uid, owner_email=email, run_day=day)
        for i, (turn_id, question, at) in enumerate(asked, start=1):
            qid = await runs._insert_question(conn, None, run_id=run["id"], seq=i, row={"question": question}, org=org,
                                              source="sync", asked_at=at)
            await conn.execute(text(
                "INSERT INTO tracelab.lab_question_traces (question_id, turn_id, organization_id, source) "
                "VALUES (CAST(:q AS uuid), :t, CAST(:o AS uuid), 'sync')"), {"q": qid, "t": turn_id, "o": org})
    return runs.run_code(run["run_no"])


async def _stored(lab):
    async with lab.connect() as conn:
        return [dict(r) for r in (await conn.execute(text(
            "SELECT run_no, title, kind, owner_user_id::text AS owner, owner_email, organization_id::text AS org, "
            "run_day::text AS day, (SELECT count(*) FROM tracelab.lab_questions q WHERE q.run_id = r.id) AS n "
            "FROM tracelab.lab_runs r ORDER BY run_no"))).mappings().all()]


def _admin(org):
    return member(org=org, all_companies=False, role="admin", email=f"admin@{org[:4]}.test")


async def _two_people_one_day(main):
    """17 Nov: ann and bob (company A) and cat (company B) each ask, 09:00 to 12:00."""
    await _turn(main, "t-ann-1", org=ORG, uid=U_ANN, email=ANN, at=DAY, question="Which vendors are blocked?",
                session=CHAT_A)
    await _turn(main, "t-bob-1", org=ORG, uid=U_BOB, email=BOB, at=DAY + timedelta(hours=1), question="How many work orders?",
                session=CHAT_B)
    await _turn(main, "t-ann-2", org=ORG, uid=U_ANN, email=ANN, at=DAY + timedelta(hours=2), question="And which are lapsed?",
                session=CHAT_A)
    await _turn(main, "t-cat-1", org=OTHER_ORG, uid=U_CAT, email=CAT, at=DAY + timedelta(hours=3), question="What EPC rating?",
                session=CHAT_C)


async def test_two_people_of_one_company_share_the_days_run(lab_scratch, main_scratch):
    await _turn(main_scratch, "t-ann-1", org=ORG, uid=U_ANN, email=ANN, at=DAY, question="Which vendors are blocked?")
    await _turn(main_scratch, "t-bob-1", org=ORG, uid=U_BOB, email=BOB, at=DAY + timedelta(hours=1), question="How many work orders?")
    await _turn(main_scratch, "t-ann-2", org=ORG, uid=U_ANN, email=ANN, at=DAY + timedelta(hours=2), question="And which are lapsed?")
    out = await runs.sync_live(None, now=NOW)
    assert (out["new_runs"], out["new_questions"]) == (1, 3), out
    (only,) = await _stored(lab_scratch)
    assert only["title"] == "Live questions · 2026-11-17" and only["kind"] == "production" and only["n"] == 3
    assert only["owner_email"] is None, "no person owns the company's day"
    assert only["org"] == ORG and only["day"] == "2026-11-17"
    listed = (await runs.list_runs(_admin(ORG), kind="production"))["runs"]
    assert len(listed) == 1
    day = listed[0]
    assert (day["n_questions"], day["people"], day["codes"]) == (3, [ANN, BOB], ["R1"])
    qs = await runs.list_questions(_admin(ORG), "R1")
    assert [q["code"] for q in qs["questions"]] == ["R1-3", "R1-2", "R1-1"], "numbered in the order they were asked"
    assert [q["question"] for q in reversed(qs["questions"])] == [
        "Which vendors are blocked?", "How many work orders?", "And which are lapsed?"]


async def test_a_company_has_one_run_per_day_and_two_syncs_never_make_two(lab_scratch, main_scratch):
    await _turn(main_scratch, "t-ann-1", org=ORG, uid=U_ANN, email=ANN, at=DAY, question="One")
    await _turn(main_scratch, "t-bob-1", org=ORG, uid=U_BOB, email=BOB, at=DAY + timedelta(minutes=5), question="Two")
    first, second = await asyncio.gather(runs.sync_live(None, now=NOW), runs.sync_live(None, now=NOW))
    assert first["new_runs"] + second["new_runs"] == 1 and first["new_questions"] + second["new_questions"] == 2
    assert [(r["n"], r["owner_email"]) for r in await _stored(lab_scratch)] == [(2, None)]
    # A later answer the same day goes to the same run, numbered on.
    await _turn(main_scratch, "t-cat-1", org=ORG, uid=U_CAT, email=CAT, at=DAY + timedelta(hours=4), question="Three")
    again = await runs.sync_live(None, now=NOW)
    assert (again["new_runs"], again["new_questions"]) == (0, 1)
    assert [r["n"] for r in await _stored(lab_scratch)] == [3]
    # The next day, and another company the same day, are other runs.
    await _turn(main_scratch, "t-ann-3", org=ORG, uid=U_ANN, email=ANN, at=DAY + timedelta(days=1), question="Four")
    await _turn(main_scratch, "t-dan-1", org=OTHER_ORG, uid=U_CAT, email=CAT, at=DAY + timedelta(hours=5), question="Five")
    more = await runs.sync_live(None, now=NOW)
    assert more["new_runs"] == 2 and more["new_questions"] == 2
    assert sorted((r["org"], r["day"], r["n"]) for r in await _stored(lab_scratch)) == sorted([
        (ORG, "2026-11-17", 3), (ORG, "2026-11-18", 1), (OTHER_ORG, "2026-11-17", 1)])


async def test_per_person_runs_of_one_day_are_shown_as_one_and_any_code_opens_it(lab_scratch, main_scratch):
    r1 = await _old_run(lab_scratch, org=ORG, uid=U_ANN, email=ANN, day=DAY.date(), asked=[
        ("t-old-1", "Which vendors are blocked?", DAY), ("t-old-2", "And which are lapsed?", DAY + timedelta(minutes=30))])
    r2 = await _old_run(lab_scratch, org=ORG, uid=U_BOB, email=BOB, day=DAY.date(), asked=[
        ("t-old-3", "How many work orders?", DAY + timedelta(hours=1))])
    r_other_day = await _old_run(lab_scratch, org=ORG, uid=U_ANN, email=ANN, day=DAY.date() - timedelta(days=1),
                                 asked=[("t-old-4", "Day before", DAY - timedelta(days=1))])
    assert (r1, r2, r_other_day) == ("R1", "R2", "R3")
    admin = _admin(ORG)
    listed = (await runs.list_runs(admin, kind="production"))["runs"]
    assert [(r["code"], r["codes"], r["n_questions"], r["people"]) for r in listed] == [
        ("R1", ["R1", "R2"], 3, [ANN, BOB]), ("R3", ["R3"], 1, [ANN])], "one entry per day, newest first"
    assert (await runs.list_runs(admin, kind="production"))["total"] == 2
    assert [r["code"] for r in (await runs.list_runs(admin, kind="production", for_page=True))["runs"]] == ["R1", "R3"]
    # Any code of the day opens the day (the first run's view); a question code opens its own question.
    for code in ("R1", "R2"):
        got = await runs.get_run(admin, code)
        assert (got["code"], got["codes"], got["n_questions"], got["people"]) == ("R1", ["R1", "R2"], 3, [ANN, BOB])
    assert (await runs.get_question(admin, "R2-1"))["question"]["question"] == "How many work orders?"
    # Its questions are the day's, each coded by the run it is filed in (newest first).
    assert [q["code"] for q in (await runs.list_questions(admin, "R2"))["questions"]] == ["R2-1", "R1-2", "R1-1"]
    assert [q["code"] for q in (await runs.list_questions(admin, "R1"))["questions"]] == ["R2-1", "R1-2", "R1-1"]
    # A search by a person finds the day that person asked in, by any of its runs' words.
    assert [r["code"] for r in (await runs.list_runs(admin, q="bob@"))["runs"]] == ["R1"]
    assert [r["code"] for r in (await runs.list_runs(admin, q="R2"))["runs"]] == ["R1"]
    # Today's answers from another person go to a company run, which then stands for the company's day.
    await _turn(main_scratch, "t-cat-1", org=ORG, uid=U_CAT, email=CAT, at=DAY + timedelta(hours=3), question="Late one")
    assert (await runs.sync_live(None, now=NOW))["new_runs"] == 1
    day = next(r for r in (await runs.list_runs(admin, kind="production"))["runs"] if "R1" in r["codes"])
    assert (day["code"], day["codes"], day["n_questions"], day["people"]) == ("R4", ["R1", "R2", "R4"], 4, [ANN, BOB, CAT])
    assert (await runs.get_run(admin, "R1"))["code"] == "R4"
    # The platform team's day stands for the lowest run of the day.
    wide = (await runs.list_runs(member(), kind="production"))["runs"]
    assert [(r["code"], r["codes"], r["n_questions"]) for r in wide] == [("R1", ["R1", "R2", "R4"], 4), ("R3", ["R3"], 1)]


async def test_the_platform_team_sees_one_run_for_the_day_and_a_company_only_its_own_part(lab_scratch, main_scratch):
    await _two_people_one_day(main_scratch)
    out = await runs.sync_live(None, now=NOW)
    assert (out["new_runs"], out["new_questions"]) == (2, 4)
    assert [(r["org"], r["n"]) for r in await _stored(lab_scratch)] == [(ORG, 3), (OTHER_ORG, 1)]
    wide = (await runs.list_runs(member(), kind="production"))["runs"]
    assert len(wide) == 1, "one run for the day, whatever the company"
    day = wide[0]
    assert (day["code"], day["codes"], day["n_questions"], day["people"]) == ("R1", ["R1", "R2"], 4, [ANN, BOB, CAT])
    assert (await runs.list_runs(member(), kind="production"))["total"] == 1
    # The companies of the day and who asked in each (the Loop names a company by an asker's email domain).
    assert set(day["organization_ids"]) == {ORG, OTHER_ORG}
    assert day["people_by_company"] == {ORG: [ANN, BOB], OTHER_ORG: [CAT]}
    for code in ("R1", "R2"):
        assert (await runs.get_run(member(), code))["codes"] == ["R1", "R2"]
    mine = (await runs.list_runs(_admin(ORG), kind="production"))["runs"]
    assert [(r["code"], r["codes"], r["n_questions"], r["people"]) for r in mine] == [("R1", ["R1"], 3, [ANN, BOB])]
    assert mine[0]["organization_ids"] == [ORG] and mine[0]["people_by_company"] == {ORG: [ANN, BOB]}
    theirs = (await runs.list_runs(_admin(OTHER_ORG), kind="production"))["runs"]
    assert [(r["code"], r["codes"], r["n_questions"], r["people"]) for r in theirs] == [("R2", ["R2"], 1, [CAT])]
    with pytest.raises(NotFound):
        await runs.get_run(_admin(ORG), "R2")
    with pytest.raises(NotFound):
        await runs.get_question(_admin(ORG), "R2-1")
    # Narrowed to one company by the platform team, the day is that company's part.
    narrowed = (await runs.list_runs(member(), kind="production", company=OTHER_ORG))["runs"]
    assert [(r["code"], r["codes"], r["n_questions"], r["people"]) for r in narrowed] == [("R2", ["R2"], 1, [CAT])]


async def test_a_days_conversations_say_who_asked_each(lab_scratch, main_scratch):
    await _two_people_one_day(main_scratch)
    await runs.sync_live(None, now=NOW)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("t-ann-1", "t-ann-2"), org=ORG, at=DAY)
    admin = _admin(ORG)
    out = await eval_store.run_sessions(admin, "R1")
    assert out["mode"] == "live" and out["run"]["code"] == "R1" and out["run"]["people"] == [ANN, BOB]
    assert [(s["id"], s["asked_by"], s["turn_ids"]) for s in out["sessions"]] == [(CHAT_A, ANN, ["t-ann-1", "t-ann-2"])]
    assert [(w["turn_id"], w["asked_by"]) for w in out["waiting"]] == [("t-bob-1", BOB)]
    assert out["companies"] == [], "one company, nothing to name"
    # Bob's conversation is filed: both are rows, each with its asker.
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("t-bob-1",), org=ORG, at=DAY + timedelta(hours=1))
    both = await eval_store.run_sessions(admin, "R1")
    assert {s["id"]: s["asked_by"] for s in both["sessions"]} == {CHAT_A: ANN, CHAT_B: BOB} and both["waiting"] == []


async def test_a_day_of_per_person_runs_lists_every_persons_conversations_and_opens_from_any_code(lab_scratch, main_scratch):
    await _old_run(lab_scratch, org=ORG, uid=U_ANN, email=ANN, day=DAY.date(), asked=[("t-old-1", "Which vendors are blocked?", DAY)])
    await _old_run(lab_scratch, org=ORG, uid=U_BOB, email=BOB, day=DAY.date(), asked=[
        ("t-old-2", "How many work orders?", DAY + timedelta(hours=1)), ("t-old-3", "And lapsed?", DAY + timedelta(hours=2))])
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("t-old-1",), org=ORG, at=DAY)
    admin = _admin(ORG)
    first, second = await eval_store.run_sessions(admin, "R1"), await eval_store.run_sessions(admin, "R2")
    assert first["run"]["code"] == second["run"]["code"] == "R1" and first["run"]["codes"] == ["R1", "R2"]
    assert [(s["id"], s["asked_by"], s["codes"]) for s in first["sessions"]] == [(CHAT_A, ANN, ["R1-1"])]
    assert [(w["code"], w["asked_by"]) for w in first["waiting"]] == [("R2-1", BOB), ("R2-2", BOB)]
    assert [(s["id"], [w["code"] for w in second["waiting"]]) for s in second["sessions"]] == [(CHAT_A, ["R2-1", "R2-2"])]


async def test_the_platform_teams_day_lists_every_companys_conversations_and_names_them(lab_scratch, main_scratch):
    async with main_scratch.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS plenum_cafm.organizations (id uuid PRIMARY KEY, name text)"))
        await conn.execute(text("INSERT INTO plenum_cafm.organizations (id, name) VALUES (CAST(:a AS uuid), 'Alpha Estates'), "
                                "(CAST(:b AS uuid), 'Bravo Property')"), {"a": ORG, "b": OTHER_ORG})
    await _two_people_one_day(main_scratch)
    await runs.sync_live(None, now=NOW)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("t-ann-1", "t-ann-2"), org=ORG, at=DAY)
    await seed_session(lab_scratch, CHAT_C, tag="chat", turn_ids=("t-cat-1",), org=OTHER_ORG, at=DAY + timedelta(hours=3))
    out = await eval_store.run_sessions(member(), "R2")
    assert out["run"]["code"] == "R1" and out["run"]["codes"] == ["R1", "R2"]
    assert {s["id"]: (s["asked_by"], s["organization_id"]) for s in out["sessions"]} == {
        CHAT_A: (ANN, ORG), CHAT_C: (CAT, OTHER_ORG)}
    assert [s["id"] for s in out["sessions"]] == [CHAT_C, CHAT_A], "newest first across companies"
    assert [(w["turn_id"], w["asked_by"], w["organization_id"]) for w in out["waiting"]] == [("t-bob-1", BOB, ORG)]
    assert {c["id"]: c["name"] for c in out["companies"]} == {ORG: "Alpha Estates", OTHER_ORG: "Bravo Property"}
    # A company member gets only their own part: no other company's conversation, asker, name or question.
    own = await eval_store.run_sessions(_admin(ORG), "R1")
    assert [s["id"] for s in own["sessions"]] == [CHAT_A] and own["companies"] == []
    assert own["run"]["people"] == [ANN, BOB] and own["run"]["codes"] == ["R1"] and own["run"]["n_questions"] == 3
    assert CAT not in str(own) and CHAT_C not in str(own) and "t-cat-1" not in str(own)
    with pytest.raises(NotFound):
        await eval_store.run_sessions(_admin(ORG), "R2")
    theirs = await eval_store.run_sessions(_admin(OTHER_ORG), "R2")
    assert [s["id"] for s in theirs["sessions"]] == [CHAT_C] and theirs["waiting"] == []
    assert ANN not in str(theirs) and BOB not in str(theirs) and CHAT_A not in str(theirs)


async def test_the_days_labels_and_progress_cover_every_run_of_the_day(lab_scratch, main_scratch):
    await _two_people_one_day(main_scratch)
    await runs.sync_live(None, now=NOW)
    await _old_run(lab_scratch, org=ORG, uid=U_BOB, email=BOB, day=DAY.date(), asked=[("t-old-9", "An older one", DAY)])
    admin = _admin(ORG)
    grid = await runs.matrix(admin, "R1")
    assert grid["run"]["codes"] == ["R1", "R3"] and grid["total"] == 4
    assert sorted(q["code"] for q in grid["questions"]) == ["R1-1", "R1-2", "R1-3", "R3-1"], "each names the run it is filed in"
    assert [q["code"] for q in (await runs.matrix(admin, "R3"))["questions"]] == [q["code"] for q in grid["questions"]]
    prog = await runs.progress(admin, "R3")
    assert prog["tiles"]["questions"] == 4 and prog["run"]["code"] == "R1"
    assert (await runs.progress(admin))["tiles"]["questions"] == 4, "the newest day when none is named"
    everyone = await runs.matrix(member(), "R1")
    assert everyone["total"] == 5 and everyone["run"]["codes"] == ["R1", "R2", "R3"]
    other = await runs.matrix(_admin(OTHER_ORG), "R2")
    assert [q["code"] for q in other["questions"]] == ["R2-1"] and other["total"] == 1
    with pytest.raises(NotFound):
        await runs.matrix(_admin(OTHER_ORG), "R1")
    # Labels for All runs: the day is one group, its questions once.
    every = await runs.matrix_all(member())
    assert [g["run"]["code"] for g in every["runs"]] == ["R1"] and every["total"] == 5
    assert sorted(q["code"] for q in every["runs"][0]["questions"]) == ["R1-1", "R1-2", "R1-3", "R2-1", "R3-1"]
    own = await runs.matrix_all(admin)
    assert [g["run"]["code"] for g in own["runs"]] == ["R1"] and own["total"] == 4
    assert not any(q["code"].startswith("R2") for q in own["runs"][0]["questions"]), "another company's rows stay out"


async def test_nothing_is_changed_to_show_a_day_as_one(lab_scratch, main_scratch):
    """The runs, questions and links are append-only: showing a day merged writes nothing and removes nothing."""
    await _old_run(lab_scratch, org=ORG, uid=U_ANN, email=ANN, day=DAY.date(), asked=[("t-old-1", "One", DAY)])
    await _old_run(lab_scratch, org=ORG, uid=U_BOB, email=BOB, day=DAY.date(), asked=[("t-old-2", "Two", DAY)])
    before = await _stored(lab_scratch)
    await runs.list_runs(_admin(ORG))
    await runs.get_run(_admin(ORG), "R2")
    await eval_store.run_sessions(_admin(ORG), "R2")
    await runs.matrix(_admin(ORG), "R2")
    await runs.progress(_admin(ORG), "R2")
    assert await _stored(lab_scratch) == before and [r["title"] for r in before] == [f"{ANN} · 2026-11-17", f"{BOB} · 2026-11-17"]
