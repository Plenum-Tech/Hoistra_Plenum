"""A Hoistra consultant's whole Trace Lab workspace holds only the questions they asked (23 Nov 2026, Hussain: "i need
to see everything of the tracelab here but the question should be consultant account specific"), on a throwaway
Postgres.

One company's live day holds the consultant's two questions and a colleague's one; the next day holds only the
colleague's. As the consultant: Runs lists the first day only, counting and naming their two questions; the day opens
on those two, its sessions are theirs and nobody else's question waits in it; the colleague's question, its turn and
the colleague's own day are not found; Labels, Progress, the Loop and the Learning draft read the same two. A company
admin still sees all three. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from eval_fixtures import ORG, UID, member, seed_session
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import eval_store, labelling, learning, loop, runs
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

U_BOB = str(uuid.uuid4())
ANN, BOB = "ann@a.test", "bob@a.test"
DAY = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
NOW = DAY + timedelta(days=3)
CHAT_A = "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70"


async def _turn(main, tid, *, uid, email, at, question, session):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, :sid, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, 'An answer.', 'single:wo_engine',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "sid": session, "org": ORG, "uid": uid, "email": email, "q": question, "at": at})


def _consultant():
    """The consultant is the fixtures' UID, so seed_session's sessions are the ones they ran."""
    from src.services.principal import Principal

    return Principal(user_id=uuid.UUID(UID), email=ANN, organization_id=uuid.UUID(ORG), role="user", can_ingest=False,
                     building_ids=(uuid.uuid4(),), access_role="consultant")


async def _as_consultant():
    m = await labelling.access(_consultant(), None, all_companies_ok=True)
    assert m.own_only and m.user_id == UID
    return m


U_ADMIN = str(uuid.uuid4())


def _admin():
    """The company admin: another person (member() would otherwise share the consultant's id, UID)."""
    return member(org=ORG, all_companies=False, role="admin", email="admin@a.test", user_id=U_ADMIN)


async def _two_days(main, lab):
    """17 Nov: ann (the consultant) asks twice, bob once in between; 18 Nov: only bob."""
    await _turn(main, "t-ann-1", uid=UID, email=ANN, at=DAY, question="Which vendors are blocked?", session=CHAT_A)
    await _turn(main, "t-bob-1", uid=U_BOB, email=BOB, at=DAY + timedelta(hours=1), question="How many work orders?",
                session="bob-chat")
    await _turn(main, "t-ann-2", uid=UID, email=ANN, at=DAY + timedelta(hours=2), question="And which are lapsed?",
                session=CHAT_A)
    await _turn(main, "t-bob-2", uid=U_BOB, email=BOB, at=DAY + timedelta(days=1), question="Bob alone",
                session="bob-chat-2")
    out = await runs.sync_live(None, now=NOW)
    assert (out["new_runs"], out["new_questions"]) == (2, 4), out
    await seed_session(lab, CHAT_A, tag="chat", turn_ids=("t-ann-1", "t-ann-2"), org=ORG, at=DAY)


async def test_runs_list_only_the_days_with_their_questions_and_count_only_those(lab_scratch, main_scratch):
    await _two_days(main_scratch, lab_scratch)
    m = await _as_consultant()
    listed = await runs.list_runs(m, limit=50)
    assert listed["total"] == 1
    (day,) = listed["runs"]
    assert (day["code"], day["n_questions"], day["n_traced"], day["people"]) == ("R1", 2, 2, [ANN])
    assert [r["code"] for r in (await runs.list_runs(m, for_page=True))["runs"]] == ["R1"]
    # A search never finds a day by a colleague's email.
    assert (await runs.list_runs(m, q="bob"))["total"] == 0
    assert (await runs.get_run(m, "R1"))["n_questions"] == 2
    with pytest.raises(NotFound):
        await runs.get_run(m, "R2")
    # The admin still sees everything.
    admin = (await runs.list_runs(_admin()))["runs"]
    assert sorted((r["code"], r["n_questions"]) for r in admin) == [("R1", 3), ("R2", 1)]
    assert next(r for r in admin if r["code"] == "R1")["people"] == [ANN, BOB]


async def test_a_day_opens_on_their_questions_and_sessions_only(lab_scratch, main_scratch):
    await _two_days(main_scratch, lab_scratch)
    m = await _as_consultant()
    qs = await runs.list_questions(m, "R1")
    assert [(q["code"], q["question"]) for q in qs["questions"]] == [
        ("R1-3", "And which are lapsed?"), ("R1-1", "Which vendors are blocked?")]
    assert (await runs.get_question(m, "R1-1"))["question"]["question"] == "Which vendors are blocked?"
    for code in ("R1-2", "R2-1"):
        with pytest.raises(NotFound):
            await runs.get_question(m, code)
    sessions = await eval_store.run_sessions(m, "R1")
    assert [s["id"] for s in sessions["sessions"]] == [CHAT_A]
    assert sessions["waiting"] == [], "a colleague's waiting question is not shown to the consultant"
    admin_sessions = await eval_store.run_sessions(_admin(), "R1")
    assert [w["code"] for w in admin_sessions["waiting"]] == ["R1-2"]


async def test_a_colleagues_turn_is_not_found_and_their_own_is(lab_scratch, main_scratch):
    await _two_days(main_scratch, lab_scratch)
    m = await _as_consultant()
    assert (await runs.turn_expected(m, "t-ann-2"))["question"] == "R1-3"
    with pytest.raises(NotFound):
        await runs.turn_expected(m, "t-bob-1")


async def test_labels_progress_and_the_loop_read_their_questions_only(lab_scratch, main_scratch):
    await _two_days(main_scratch, lab_scratch)
    m = await _as_consultant()
    grid = await runs.matrix(m, "R1")
    assert sorted(q["code"] for q in grid["questions"]) == ["R1-1", "R1-3"] and grid["total"] == 2
    every = await runs.matrix_all(m)
    assert [(g["run"]["code"], len(g["questions"])) for g in every["runs"]] == [("R1", 2)]
    prog = await runs.progress(m)
    assert prog["run"]["code"] == "R1" and prog["tiles"]["questions"] == 2
    assert (await runs.progress(_admin(), "R1"))["tiles"]["questions"] == 3
    # The Loop and its waiting list read with the same scope (no failure modes here: the SQL is what is checked).
    assert (await loop.overview(m))["modes"] == []
    waiting = await loop.proposals(m)
    assert waiting["expected"] == [] and waiting["labels"] == []


async def test_the_learning_draft_is_drawn_from_their_questions_and_their_own_marks(lab_scratch, main_scratch):
    await _two_days(main_scratch, lab_scratch)
    admin, m = _admin(), await _as_consultant()
    for code, said in (("R1-3", "Three are lapsed."), ("R1-2", "Forty work orders.")):
        await runs.set_expected(admin, code, expected=said)
    await runs.set_expected(m, "R1-1", expected="Two vendors are blocked.")
    mine = await learning.gather(ORG, own_uid=UID)
    assert [e["question"] for e in mine["expected"]] == ["Which vendors are blocked?"], "the admin's on R1-3 is not theirs"
    everyone = await learning.gather(ORG)
    assert sorted(e["question"] for e in everyone["expected"]) == [
        "And which are lapsed?", "How many work orders?", "Which vendors are blocked?"]


async def test_on_their_own_questions_they_see_only_the_marks_they_made(lab_scratch, main_scratch, monkeypatch):
    """23 Nov 2026, Hussain: "it should be new" - only their own marks, and only their own failure modes. An admin's
    expected answer and failure-mode cell on the consultant's question are not shown to the consultant; the platform's
    failure mode is not theirs to see or use; their own mode is theirs alone; the admin still sees all of their own."""
    await _two_days(main_scratch, lab_scratch)
    admin, m = _admin(), await _as_consultant()

    async def consultants(ids):
        return {i for i in ids if i == UID}
    monkeypatch.setattr(labelling, "hoistra_consultants", consultants)
    platform = (await runs.save_mode(member(user_id=str(uuid.uuid4())), name="Missed a building",
                                     definition="A building at risk is left out."))["lineage_id"]
    mine = (await runs.save_mode(m, name="Wrong month", definition="It reads another month."))["lineage_id"]
    assert [x["lineage_id"] for x in await runs.list_modes(m)] == [mine]
    assert [x["lineage_id"] for x in await runs.list_modes(admin)] == [platform]
    await runs.set_expected(admin, "R1-3", expected="Three are lapsed.")
    await runs.set_cell(admin, "R1-3", mode=platform, value="positive")
    await runs.set_cell(m, "R1-1", mode=mine, value="negative")
    with pytest.raises(NotFound):
        await runs.set_cell(m, "R1-1", mode=platform, value="positive")
    with pytest.raises(NotFound):
        await runs.set_cell(admin, "R1-1", mode=mine, value="positive")
    # The question page: the admin's expected answer is not theirs.
    assert (await runs.get_question(m, "R1-3"))["expected"] is None
    assert (await runs.get_question(admin, "R1-3"))["expected"]["expected"] == "Three are lapsed."
    assert (await runs.turn_expected(m, "t-ann-2"))["expected"] is None
    # Their run's counts and question rows: no expected answer of the admin's.
    assert (await runs.get_run(m, "R1"))["n_expected"] == 0
    rows = {q["code"]: q for q in (await runs.list_questions(m, "R1"))["questions"]}
    assert rows["R1-3"]["expected"] is None and rows["R1-3"]["has_expected"] is False
    # Labels: their mode only, and only the cell they set; the admin's grid never carries the consultant's cell.
    grid = await runs.matrix(m, "R1")
    assert [x["lineage_id"] for x in grid["modes"]] == [mine]
    assert set(grid["cells"]) == {rows["R1-1"]["id"]} and grid["cells"][rows["R1-1"]["id"]][mine][0]["mine"]
    admin_grid = await runs.matrix(admin, "R1")
    assert [x["lineage_id"] for x in admin_grid["modes"]] == [platform]
    assert all(mine not in by for by in admin_grid["cells"].values())
    # Progress and the Loop: their mode, their cell.
    prog = await runs.progress(m, "R1")
    assert [x["lineage_id"] for x in prog["modes"]] == [mine] and prog["tiles"]["cells_filled"] == 1
    assert [x["lineage_id"] for x in (await loop.overview(m))["modes"]] == [mine]
    assert [x["lineage_id"] for x in (await loop.overview(admin))["modes"]] == [platform]
    assert (await runs.progress(admin, "R1"))["tiles"]["cells_filled"] == 1, "the admin's own cell only"
    # Their mode is theirs to retire; the platform's is not found for them.
    with pytest.raises(NotFound):
        await runs.retire_mode(m, platform)
    assert (await runs.retire_mode(m, mine))["retired"] is True
