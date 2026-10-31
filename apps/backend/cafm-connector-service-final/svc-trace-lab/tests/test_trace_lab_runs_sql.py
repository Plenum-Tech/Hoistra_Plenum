"""Runs on a throwaway Postgres (22 Oct 2026): live answers become one run per person per company per day with serial
codes; a company sees only its own runs and the platform team and a consultant see every company's; a question shows its
answer, its expected answer (versions) and free-text notes with no Right / Wrong tag; an import adds expected answers by
question code or a new synthetic run; failure modes are the labels view's columns and two people's different cells both
show; progress counts it all. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import labelling, runs
from trace_lab.review_errors import Forbidden, Invalid, NotFound
from trace_lab_m2_fixtures import principal

pytestmark = requires_scratch

ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
DAY1 = (datetime.now(timezone.utc) - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
DAY2 = DAY1 + timedelta(days=1)


async def _turn(main, tid, *, org, uid, email, at, question, session="s-1"):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, 'An answer.', 'single:wo_engine',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "sid": session, "org": org, "uid": uid, "email": email, "q": question, "at": at})


async def _users(main, *people):
    async with main.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS plenum_cafm.users (id uuid PRIMARY KEY, email text, "
                                "organization_id uuid, platform_role text)"))
        for p, org in people:
            await conn.execute(text("INSERT INTO plenum_cafm.users (id, email, organization_id, platform_role) VALUES "
                                    "(CAST(:i AS uuid), :e, CAST(:o AS uuid), :r) ON CONFLICT (id) DO NOTHING"),
                               {"i": str(p.user_id), "e": p.email, "o": org, "r": p.role})


async def test_runs_from_live_answers_to_labels_and_progress(main_scratch, lab_scratch):  # noqa: F811
    u1, u2, u3 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    await _turn(main_scratch, "turn-a1", org=ORG_A, uid=u1, email="ann@a.test", at=DAY1, question="Which vendors are blocked?")
    await _turn(main_scratch, "turn-b1", org=ORG_A, uid=u2, email="bob@a.test", at=DAY1 + timedelta(minutes=30),
                question="How many work orders?")
    await _turn(main_scratch, "turn-c1", org=ORG_B, uid=u3, email="cat@b.test", at=DAY1 + timedelta(hours=1),
                question="What EPC rating?")
    await _turn(main_scratch, "turn-a2", org=ORG_A, uid=u1, email="ann@a.test", at=DAY1 + timedelta(hours=2),
                question="And which are lapsed?")
    await _turn(main_scratch, "turn-a3", org=ORG_A, uid=u1, email="ann@a.test", at=DAY2, question="Next day question")
    await _turn(main_scratch, "turn-gold", org=ORG_A, uid=u1, email="ann@a.test", at=DAY1, question="Golden",
                session="golden-abc-1-r1")

    boss_p = principal(None, "superadmin", email="boss@plenum.test")
    boss = await labelling.access(boss_p, None, all_companies_ok=True)
    assert (boss.role, boss.all_companies, boss.org) == ("platform", True, None)
    with pytest.raises(HTTPException):
        await labelling.access(boss_p, None)                     # older routes still need a company named

    first = await runs.sync_production(boss)
    assert (first["new_runs"], first["new_questions"]) == (4, 5), first
    assert (await runs.sync_production(boss))["new_questions"] == 0, "a second sync numbers nothing twice"

    every = await runs.list_runs(boss)
    assert every["total"] == 4
    by_code = {r["code"]: r for r in every["runs"]}
    assert by_code["R1"]["owner_email"] == "ann@a.test" and by_code["R1"]["n_questions"] == 2
    assert by_code["R1"]["kind"] == "production" and by_code["R4"]["run_day"] == DAY2.date().isoformat()
    assert by_code["R3"]["organization_id"] == ORG_B
    qs = await runs.list_questions(boss, "R1")
    assert [(q["code"], q["question"], q["turn_ids"]) for q in qs["questions"]] == [
        ("R1-1", "Which vendors are blocked?", ["turn-a1"]), ("R1-2", "And which are lapsed?", ["turn-a2"])]

    admin = await labelling.access(principal(ORG_A, "admin", email="adm@a.test"), None, all_companies_ok=True)
    assert {r["code"] for r in (await runs.list_runs(admin))["runs"]} == {"R1", "R2", "R4"}
    with pytest.raises(NotFound):
        await runs.get_run(admin, "R3")                          # another company's run stays unseen
    with pytest.raises(NotFound):
        await runs.get_question(admin, "R3-1")

    page = await runs.get_question(boss, "R1-2")
    assert page["question"]["code"] == "R1-2" and page["active"]["turn_id"] == "turn-a2"
    assert page["active"]["organization_id"] == ORG_A and page["expected"] is None and page["labels"] == []
    for_a = labelling.for_org(boss, ORG_A)
    out_note = await labelling.save_label(for_a, "turn-a2", step_key="turn-a2:orchestrator#output", label=None,
                                          note="It should not have called a tool here.", step_title="Orchestrator output")
    await labelling.save_label(for_a, "turn-a2", step_key="turn-a2:scenario", label=None,
                               note="A follow-up: keep the vendor filter.", step_title="The question")
    assert out_note["label"] is None and out_note["step_kind"] == "output" and out_note["question_code"] == "R1-2"
    with pytest.raises(Invalid):
        await labelling.save_label(for_a, "turn-a2", step_key="turn-a2:scenario", label=None, note="  ", step_title=None)
    assert len((await runs.get_question(boss, "R1-2"))["labels"]) == 2
    every_note = await labelling.list_labels(boss)               # the Notes page, with no company named
    assert every_note["total"] == 2 and every_note["everyone"] and {x["question_code"] for x in every_note["labels"]} == {"R1-2"}

    exp = await runs.set_expected(boss, "R1-2", expected="Lists the 3 lapsed vendors.", expected_id="report_lapsed",
                                  evidence={"reference": "SELECT * FROM vendors", "type": "sql"})
    assert (exp["expected"], exp["source"], exp["evidence"]["type"]) == ("Lists the 3 lapsed vendors.", "manual", "sql")
    await runs.set_expected(boss, "R1-2", expected="Lists the lapsed vendors with dates.")
    page = await runs.get_question(boss, "R1-2")
    assert page["expected"]["expected"].endswith("with dates.") and len(page["expected_history"]) == 1

    done = await runs.import_file(boss, "expected.csv", b"Question code,Expected outcome\nR2-1,Gives the count\nR9-9,x\n")
    assert done["expected_added"] == 1 and done["questions_added"] == 0
    assert done["problems"] == [{"row": 3, "error": "No question R9-9 in Trace Lab."}]
    made = await runs.import_file(boss, "support batch.csv",
                                  b"Question,Role,Intent,Expected answer,Follow-ups\n"
                                  b"What is the status of my order 3722?,shopper,order status,Report placed,Double-check\n")
    assert (made["run"], made["questions_added"], made["expected_added"]) == ("R5", 1, 1)
    r5 = await runs.list_questions(boss, "R5")
    assert r5["run"]["kind"] == "synthetic" and r5["run"]["title"] == "support batch"
    assert r5["questions"][0]["followups"] == ["Double-check"] and r5["questions"][0]["has_expected"]
    assert "R5" not in {r["code"] for r in (await runs.list_runs(admin))["runs"]}, "a platform-wide batch is not a company's"
    tester = labelling.Member(admin.principal, ORG_A, admin.user_id, None, "tester", "active", False)
    with pytest.raises(Forbidden):
        await runs.import_file(tester, "x.csv", b"Question\nHi\n")
    picks = await runs.answer_candidates(boss, "R5-1")             # a platform-wide question: every company, newest first
    assert {c["turn_id"] for c in picks["candidates"]} == {"turn-a1", "turn-b1", "turn-c1", "turn-a2", "turn-a3"}, "golden left out"
    assert [c["turn_id"] for c in (await runs.answer_candidates(boss, "R5-1", q="epc"))["candidates"]] == ["turn-c1"]
    assert [c["turn_id"] for c in (await runs.answer_candidates(boss, "R5-1", q="turn-a2"))["candidates"]] == ["turn-a2"]
    assert (await runs.answer_candidates(boss, "R5-1", q="100%_"))["candidates"] == [], "% and _ are literal"
    own = (await runs.answer_candidates(admin, "R1-2"))["candidates"]
    assert own and all(c["organization_id"] == ORG_A for c in own), "a company admin sees only the company's answers"
    assert "turn-a2" not in {c["turn_id"] for c in own}, "the answer R1-2 already has is left out"
    await _turn(main_scratch, "turn-same", org=ORG_B, uid=u3, email="cat@b.test", at=DAY1 - timedelta(hours=5),
                question="what is the status of my order 3722? ")
    first = (await runs.answer_candidates(boss, "R5-1"))["candidates"][0]
    assert (first["turn_id"], first["same"]) == ("turn-same", True), "the same question, word for word, comes first"
    with pytest.raises(Forbidden):
        await runs.answer_candidates(tester, "R1-1")
    linked = await runs.link_trace(boss, "R5-1", "turn-c1")
    assert linked["active"]["turn_id"] == "turn-c1" and linked["active"]["organization_id"] == ORG_B
    assert "turn-c1" not in {c["turn_id"] for c in (await runs.answer_candidates(boss, "R5-1"))["candidates"]}

    consultant_p = principal(ORG_A, "user", email="consult@plenum.test")
    await _users(main_scratch, (consultant_p, ORG_A))
    given = await runs.decide_platform_member(boss, email="Consult@Plenum.test", status="active")
    assert (given["role"], given["status"], given["email"]) == ("consultant", "active", "consult@plenum.test")
    consultant = await labelling.access(consultant_p, None, all_companies_ok=True)
    assert (consultant.role, consultant.all_companies, consultant.manages) == ("consultant", True, False)
    assert (await runs.list_runs(consultant))["total"] == 5
    with pytest.raises(HTTPException):
        await labelling.list_members(consultant)                 # sees everything, manages nobody

    mode = await runs.save_mode(consultant, name="missing_decision_rationale",
                                definition="States an outcome without the fact and rule behind it.")
    other = await runs.save_mode(consultant, name="redundant_tool_calls", definition=None)
    with pytest.raises(Invalid):
        await runs.save_mode(consultant, name="Missing_Decision_Rationale")
    with pytest.raises(Forbidden):
        await runs.save_mode(admin, name="admins_cannot")
    renamed = await runs.save_mode(consultant, name="missing_decision_rationale", definition="Clearer definition.",
                                   lineage_id=mode["lineage_id"])
    assert renamed["lineage_id"] == mode["lineage_id"] and renamed["definition"] == "Clearer definition."
    await runs.retire_mode(consultant, other["lineage_id"])
    assert [x["name"] for x in await runs.list_modes(boss)] == ["missing_decision_rationale"]

    await runs.set_cell(boss, "R1-1", mode=mode["lineage_id"], value="positive")
    await runs.set_cell(admin, "R1-1", mode=mode["lineage_id"], value="negative")
    with pytest.raises(NotFound):
        await runs.set_cell(admin, "R3-1", mode=mode["lineage_id"], value="negative")
    with pytest.raises(Invalid):
        await runs.set_cell(admin, "R1-1", mode=mode["lineage_id"], value="maybe")
    grid = await runs.matrix(admin, "R1")
    cell = grid["cells"][grid["questions"][0]["id"]][mode["lineage_id"]]
    assert sorted((c["value"], c["author_email"], c["mine"]) for c in cell) == [
        ("negative", "adm@a.test", True), ("positive", "boss@plenum.test", False)], "both views show"
    assert grid["last_updated"] and [m_["name"] for m_ in grid["modes"]] == ["missing_decision_rationale"]
    await runs.set_cell(boss, "R1-1", mode=mode["lineage_id"], value=None)
    grid = await runs.matrix(boss, "R1")
    assert [c["value"] for c in grid["cells"][grid["questions"][0]["id"]][mode["lineage_id"]]] == ["negative"]

    prog = await runs.progress(boss, "R1")
    assert prog["tiles"] == {"questions": 2, "reviewed": 2, "traced": 2, "modes": 1, "cells_filled": 1, "cells_total": 2,
                             "notes": 2}
    card = prog["modes"][0]
    assert (card["positives"], card["negatives"]) == (0, 1) and card["trend"][-1] == {"run": "R1", "positives": 0}

    await runs.decide_platform_member(boss, email="consult@plenum.test", status="removed")
    after = await labelling.access(consultant_p, None, all_companies_ok=True)
    assert (after.all_companies, after.status) == (False, "none"), "removed: back to an ordinary company user"

    async with lab_scratch.begin() as conn:
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE tracelab.lab_runs SET title = 'changed'"))
