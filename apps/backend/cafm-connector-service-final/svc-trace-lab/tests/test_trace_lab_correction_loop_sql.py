"""The correction loop on a throwaway Postgres (3 Nov 2026, migration 0013).

Hussain asked for three things:
  1. Trace Lab takes in new backend and frontend traces by itself, newest first, and a sync never disturbs older traces,
     notes, labels or failure-mode decisions.
  2. People write and approve; a coding agent only proposes failure modes, labels and expected answers, and nothing it
     proposes counts until a person confirms it.
  3. Traces -> notes -> failure modes -> SPEC.md -> correction -> regression run -> rerun, with expected answers derived
     from the approved specification and the company's records, never from the model's own answer.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import autosync, labelling, loop, runs
from trace_lab.review_errors import Forbidden, Invalid, NotFound
from trace_lab_m2_fixtures import principal

pytestmark = requires_scratch

ORG = str(uuid.uuid4())
OTHER = str(uuid.uuid4())
NOW = datetime.now(timezone.utc).replace(microsecond=0)
ANSWER = "Bishopsgate Tower has 18 floors and 13 open work orders, and no vendor is blocked there at the moment."


async def _turn(main, tid, *, org, uid, email, at, question, answer=ANSWER):
    async with main.begin() as conn:
        await conn.execute(text("""
            INSERT INTO plenum_cafm.agent_trace_turns
                (turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at,
                 latency_ms, llm_calls, tool_calls, input_tokens, output_tokens, models, tools, ok)
            VALUES (:tid, 's-1', CAST(:org AS uuid), CAST(:uid AS uuid), :email, :q, :a, 'single:wo_engine',
                    :at, :at, 1000, 1, 0, 10, 10, CAST('["m"]' AS jsonb), CAST('[]' AS jsonb), true)"""),
            {"tid": tid, "org": org, "uid": uid, "email": email, "q": question, "a": answer, "at": at})


async def _count(lab, table):
    async with lab.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM tracelab.{table}"))).scalar_one()


async def _people():
    boss = await labelling.access(principal(None, "superadmin", email="boss@plenum.test"), None, all_companies_ok=True)
    admin = await labelling.access(principal(ORG, "admin", email="adm@a.test"), None, all_companies_ok=True)
    return boss, admin


async def _agent_mode(lab, name, definition, lineage=None, tombstone=False):
    async with lab.begin() as conn:
        return (await conn.execute(text(
            "INSERT INTO tracelab.lab_failure_modes (lineage_id, name, definition, tombstone, created_by_email) "
            "VALUES (CAST(:lin AS uuid), :n, :d, :t, 'agent@plenum.test') RETURNING id::text"),
            {"lin": lineage, "n": name, "d": definition, "t": tombstone})).scalar_one()


async def test_sync_by_itself_newest_first_and_nothing_older_is_touched(main_scratch, lab_scratch):  # noqa: F811
    uid = str(uuid.uuid4())
    await _turn(main_scratch, "turn-1", org=ORG, uid=uid, email="ann@a.test", at=NOW - timedelta(hours=3),
                question="How many floors does Bishopsgate Tower have?")
    await _turn(main_scratch, "turn-2", org=ORG, uid=uid, email="ann@a.test", at=NOW - timedelta(hours=2),
                question="And how many open work orders?")
    out = await autosync.tick()
    assert out["last_ok"] is True and out["last_new_questions"] == 2

    boss, admin = await _people()
    run = (await runs.list_runs(boss))["runs"][0]
    qs = (await runs.list_questions(boss, run["code"]))["questions"]
    assert [q["question"] for q in qs] == ["And how many open work orders?",
                                           "How many floors does Bishopsgate Tower have?"], "a day run lists newest first"

    # People work on what was synced; the next round must leave all of it alone.
    mode = await runs.save_mode(boss, name="wrong_floor_count", definition="States a floor count the records do not hold.")
    await runs.set_cell(admin, run["code"] + "-1", mode=mode["lineage_id"], value="positive")
    before = {t: await _count(lab_scratch, t) for t in ("lab_questions", "lab_label_cells", "lab_failure_modes",
                                                          "lab_expected_answers", "lab_runs")}
    assert (await autosync.tick())["last_new_questions"] == 0
    assert {t: await _count(lab_scratch, t) for t in before} == before

    # A browser error the chat never recorded becomes a question of that person's day; a newer answer comes first.
    who = principal(ORG, "admin", email="ann@a.test")
    who = SimpleNamespace(**{**who.__dict__, "user_id": uuid.UUID(uid)})
    kept = await loop.record_frontend_event(who, {"kind": "error", "question": "Which vendors are blocked?",
                                                  "error": "The connection closed", "view": "home", "instance": "assets",
                                                  "organization_id": OTHER})
    assert kept["kept"] is True
    async with lab_scratch.connect() as conn:
        org = (await conn.execute(text("SELECT organization_id::text FROM tracelab.lab_frontend_events"))).scalar_one()
    assert org == ORG, "the company is the caller's, never the body's"
    await _turn(main_scratch, "turn-3", org=ORG, uid=uid, email="ann@a.test", at=NOW - timedelta(minutes=5),
                question="Which certificates have lapsed?")
    assert (await autosync.tick())["last_new_questions"] == 2
    after = {t: await _count(lab_scratch, t) for t in ("lab_label_cells", "lab_failure_modes", "lab_expected_answers")}
    assert after == {k: before[k] for k in after}, "notes, labels and modes are untouched by a sync"
    qs = (await runs.list_questions(boss, run["code"]))["questions"]
    assert [q["question"] for q in qs[:2]] == ["Which vendors are blocked?", "Which certificates have lapsed?"], \
        "the browser error happened last, so it is listed first"
    browser_q = next(q for q in qs if q["question"] == "Which vendors are blocked?")
    assert browser_q["turn_ids"][0].startswith("browser:")
    page = await runs.get_question(boss, browser_q["code"])
    assert page["browser"] and page["browser"][0]["error"] == "The connection closed"


async def test_a_coding_agents_proposals_count_only_once_a_person_decides(main_scratch, lab_scratch):  # noqa: F811
    uid = str(uuid.uuid4())
    await _turn(main_scratch, "turn-p", org=ORG, uid=uid, email="ann@a.test", at=NOW - timedelta(hours=1),
                question="How many floors does Bishopsgate Tower have?")
    await runs.sync_live(None)
    boss, admin = await _people()
    code = (await runs.list_runs(boss))["runs"][0]["code"] + "-1"

    proposed = await _agent_mode(lab_scratch, "missing_blocked_vendor", "Leaves out a vendor blocked at the building.")
    assert await runs.list_modes(boss) == [], "an agent's mode is not a column until a person approves it"
    pend = await loop.proposals(boss)
    assert [x["id"] for x in pend["modes"]] == [proposed] and pend["modes"][0]["kind"] == "new"
    lin = pend["modes"][0]["lineage_id"]
    with pytest.raises(NotFound):
        await runs.set_cell(admin, code, mode=lin, value="positive")
    with pytest.raises(Forbidden):
        await loop.decide(admin, "failure_mode", proposed, decision="approved")
    await loop.decide(boss, "failure_mode", proposed, decision="approved", note="Seen in R1")
    assert [md["name"] for md in await runs.list_modes(boss)] == ["missing_blocked_vendor"]
    with pytest.raises(Invalid):
        await loop.decide(boss, "failure_mode", proposed, decision="rejected")

    # A change proposed by the agent leaves the confirmed version in force; rejected, it is gone.
    change = await _agent_mode(lab_scratch, "missing_blocked_vendor", "Something else.", lineage=lin)
    live = await runs.list_modes(boss)
    assert live[0]["definition"] == "Leaves out a vendor blocked at the building." and live[0]["change_proposed"]
    await loop.decide(boss, "failure_mode", change, decision="rejected")
    assert not (await runs.list_modes(boss))[0]["change_proposed"]
    rejected = await _agent_mode(lab_scratch, "noise", "Not a real failure.")
    await loop.decide(boss, "failure_mode", rejected, decision="rejected")
    assert [md["name"] for md in await runs.list_modes(boss)] == ["missing_blocked_vendor"]

    # An agent's label is a suggestion: shown, never counted, until a person sets it.
    async with lab_scratch.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.lab_label_cells (question_id, mode_lineage_id, value, author_email, author_role, source) "
            "VALUES (tracelab.question_id(:c), CAST(:m AS uuid), 'positive', 'agent@plenum.test', 'coding agent', "
            "'coding_agent')"), {"c": code, "m": lin})
    view = await runs.matrix(boss, code.split("-")[0])
    cell = next(iter(next(iter(view["cells"].values()))[lin]))
    assert cell["suggestion"] is True
    prog = await runs.progress(boss, code.split("-")[0])
    assert prog["modes"][0]["positives"] == 0
    await runs.set_cell(admin, code, mode=lin, value="positive")
    assert (await runs.progress(boss, code.split("-")[0]))["modes"][0]["positives"] == 1

    # An agent's expected answer is a proposal; approving needs the spec rule and the records; never the model's answer.
    async with lab_scratch.begin() as conn:
        exp_id = (await conn.execute(text(
            "INSERT INTO tracelab.lab_expected_answers (question_id, expected, evidence, source, author_email) "
            "VALUES (tracelab.question_id(:c), 'Bishopsgate Tower has 22 floors.', NULL, 'coding_agent', "
            "'agent@plenum.test') RETURNING id::text"), {"c": code})).scalar_one()
    page = await runs.get_question(boss, code)
    assert page["expected"] is None and page["expected_pending"]["id"] == exp_id
    with pytest.raises(Invalid) as e:
        await loop.decide(admin, "expected_answer", exp_id, decision="approved")
    assert e.value.reason == "not_derived"
    with pytest.raises(Invalid) as e:
        await runs.set_expected(admin, code, expected=ANSWER)
    assert e.value.reason == "copied_from_answer"


async def test_the_loop_from_notes_to_spec_correction_regression_and_rerun(main_scratch, lab_scratch, monkeypatch):  # noqa: F811
    uid = str(uuid.uuid4())
    await _turn(main_scratch, "turn-r", org=ORG, uid=uid, email="ann@a.test", at=NOW - timedelta(hours=1),
                question="How many floors does Bishopsgate Tower have?")
    await runs.sync_live(None)
    boss, admin = await _people()
    run_code = (await runs.list_runs(boss))["runs"][0]["code"]
    code = run_code + "-1"
    mode = await runs.save_mode(boss, name="wrong_floor_count", definition="States a floor count the records do not hold.")
    await runs.set_cell(admin, code, mode=mode["lineage_id"], value="positive")

    with pytest.raises(Invalid) as e:
        await loop.make_regression(boss, mode["lineage_id"], company=ORG)
    assert e.value.reason == "not_in_spec", "regression waits for the rule its expected answers come from"

    # SPEC.md: drafted from the confirmed mode, proposed and approved by people.
    with pytest.raises(Forbidden):
        await loop.draft_spec(admin)
    draft = await loop.draft_spec(boss)
    assert draft["rules"][0]["code"] == "S-1" and draft["rules"][0]["mode_lineage_id"] == mode["lineage_id"]
    v = await loop.propose_spec(boss, rules=draft["rules"], intro="Answers about buildings.")
    assert await loop.spec_markdown(boss) is None, "a proposed version is not in force"
    await loop.decide(boss, "spec_version", v["id"], decision="approved")
    md = await loop.spec_markdown(boss)
    assert "### S-1" in md and "wrong_floor_count" in md and "never from an answer the model gave" in md

    # The expected answer, derived from S-1 and the buildings record.
    with pytest.raises(Invalid):
        await runs.set_expected(admin, code, expected="Bishopsgate Tower has 22 floors.",
                                evidence={"spec_rule": "S-9", "data": "x"})
    await runs.set_expected(admin, code, expected="Bishopsgate Tower has 22 floors.",
                            evidence={"spec_rule": "s-1", "values": ["22"],
                                      "data": "SELECT floors FROM buildings WHERE name = 'Bishopsgate Tower'"})
    page = await runs.get_question(boss, code)
    assert page["expected"]["derivation"]["derived"] is True and page["spec_rules"][0]["code"] == "S-1"

    corr = await loop.add_correction(boss, kind="prompt", reference="svc-deepagents skills/buildings.md@abc123",
                                     mode=mode["lineage_id"], description="Read floors from the buildings register.")
    assert corr["kind"] == "prompt"
    with pytest.raises(Invalid):
        await loop.add_correction(boss, kind="code", reference="x", spec_rule="S-7")

    made = await loop.make_regression(boss, mode["lineage_id"], company=ORG, space="assets")
    assert (made["questions"], made["with_expected"], made["spec_rule"]) == (1, 1, "S-1")
    before = await loop.compare(boss, made["run"])
    assert before["questions"][0]["verdict"] == "not_rerun" and before["questions"][0]["from"] == code
    assert before["questions"][0]["before"]["check"]["status"] == "mismatch", "the marked answer said 18"

    from trace_lab import capture, orchestrator_host

    asked = []

    async def run_message(orch, **kw):
        asked.append(kw)
        return SimpleNamespace(answer="Bishopsgate Tower has 22 floors.", error=None, turn_id="t-new", spans=[],
                               cards=[], refused=[], turn={"answer": "Bishopsgate Tower has 22 floors."})

    async def no_overlays():
        return 0

    monkeypatch.setattr(orchestrator_host, "assert_isolated", lambda: [])
    monkeypatch.setattr(orchestrator_host, "ensure_overlays_readonly", no_overlays)
    monkeypatch.setattr(orchestrator_host, "get_orchestrator", lambda: object())
    monkeypatch.setattr(orchestrator_host, "release_session", lambda sid: None)
    monkeypatch.setattr(capture, "run_message", run_message)
    from trace_lab import agent

    async def under_test():
        return {("compliance", "planner"): "PLANNER UNDER TEST"}

    monkeypatch.setattr(agent, "under_test", under_test)
    out = await loop.rerun(boss, made["run"])
    assert out["asked"] == 1 and out["next_start"] is None
    assert asked[0]["organization_id"] == ORG and asked[0]["space"] == "assets"
    assert asked[0]["candidate"] == {("compliance", "planner"): "PLANNER UNDER TEST"}, "the rerun asks with it"
    assert out["under_test"] == ["skills/compliance/planner.md"]
    after = await loop.compare(boss, made["run"])
    assert after["questions"][0]["verdict"] == "fixed" and after["summary"]["fixed"] == 1
    newest = await runs.get_question(boss, made["run"] + "-1")
    assert newest["traces"][0]["turn_id"].startswith("scenario:"), "the newest answer is listed first"

    view = await loop.overview(boss)
    row = view["modes"][0]
    assert row["steps"] == {"annotated": True, "taxonomy": True, "spec": True, "correction": True, "regression": True,
                            "rerun": True}
    assert row["next"] is None, "every step done: nothing left to point at"
    assert row["rule"]["code"] == "S-1" and row["corrections"] == 1 and row["regression_runs"][0]["run"] == made["run"]


async def test_frontend_events_are_bounded_and_need_no_lab_membership(lab_scratch, monkeypatch):  # noqa: F811
    who = principal(ORG, "user", email="u@a.test")
    with pytest.raises(Invalid):
        await loop.record_frontend_event(who, {"kind": "clicked"})
    monkeypatch.setattr(loop, "EVENTS_PER_MINUTE", 2)
    got = [await loop.record_frontend_event(who, {"kind": "answer_shown", "turn_id": "turn-x", "ms_total": 1200,
                                                  "ms_first_text": -5, "question": "q" * 9000}) for _ in range(3)]
    assert [g["kept"] for g in got] == [True, True, False]
    async with lab_scratch.connect() as conn:
        row = (await conn.execute(text("SELECT ms_first_text, char_length(question) AS n FROM tracelab.lab_frontend_events "
                                       "LIMIT 1"))).mappings().one()
    assert row["ms_first_text"] is None and row["n"] == 4000
