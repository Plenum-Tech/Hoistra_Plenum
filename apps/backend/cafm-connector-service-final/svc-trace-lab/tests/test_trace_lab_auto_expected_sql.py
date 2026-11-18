"""Expected answers drafted from SPEC.md for live questions (14 Nov 2026, Hussain: "I want the system to add it
automatically when I ask a question on the live Hoistra platform ... an expected answer in the trace based on the spec
file"; "only for the live answers, don't change anything in the flow from Codex").

A new question asked by hand on the live platform gets one expected answer drafted by the coding agent's model from the
approved SPEC.md and the question - never Hoistra's own answer. It is a draft (source coding_agent) until a person
accepts or edits it. Codex scenario turns are never drafted for. Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text

from eval_fixtures import ANSWER, ORG, OTHER_ORG, T0, member, seed_session
from test_trace_lab_run_sessions_sql import CHAT_A, CHAT_B, NOW, _live_run, _main, _old_day
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import agent, auto_expected, loop, runs
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

RULES = [{"code": "S-1", "title": "overdue_reported_as_none",
          "text": "When the register holds overdue items, list each one with its days overdue; never report none."},
         {"code": "S-4", "title": "list_answer_as_paragraph", "text": "A list question is answered as a list."}]


@pytest.fixture(autouse=True)
def _no_rests(monkeypatch):
    monkeypatch.setattr(auto_expected, "_resting", {})


async def _spec():
    boss = member()
    v = await loop.propose_spec(boss, rules=RULES, intro="Answers about compliance.")
    await loop.decide(boss, "spec_version", v["id"], decision="approved")
    return v["version"]


def _model(monkeypatch, reply=None, fail=False):
    asked = []

    async def fake(system, prompt, schema, max_tokens):
        asked.append({"system": system, "prompt": prompt, "schema": schema})
        if fail:
            raise RuntimeError("the model is not reachable")
        return reply or {"expected": "Lists each overdue certificate with its days overdue, as a list.",
                         "rules": ["S-1", "S-4"]}
    monkeypatch.setattr(agent, "_ask", fake)
    return asked


async def _drafts(lab):
    async with lab.connect() as c:
        return [dict(r) for r in (await c.execute(text(
            "SELECT e.question_id::text AS qid, e.expected, e.evidence, e.source, e.author_email"
            " FROM tracelab.lab_expected_answers e ORDER BY e.created_at"))).mappings().all()]


async def test_a_new_live_question_gets_a_draft_from_the_spec(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    version = await _spec()
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out == {"drafted": 3, "failed": 0, "keyed": 0, "replaced": 0, "redrafted": 0, "skipped": None}
    rows = await _drafts(lab_scratch)
    assert len(rows) == 3 and {r["source"] for r in rows} == {"coding_agent"}
    assert {r["author_email"] for r in rows} == {agent.AGENT_EMAIL}
    assert rows[0]["evidence"] == {"drafted_from": "SPEC.md", "spec_version": version, "rules": ["S-1", "S-4"],
                                    "draft": auto_expected.DRAFT_VERSION}
    prompt = asked[0]["prompt"]
    assert "S-1" in prompt and "never report none" in prompt, "the approved rules are in the prompt"
    assert "Which vendors are blocked?" in " ".join(a["prompt"] for a in asked), "each question is in its own prompt"
    assert ANSWER not in " ".join(a["prompt"] for a in asked), "never Hoistra's own answer"


async def test_unknown_rule_codes_are_dropped(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch, reply={"expected": "A list.", "rules": ["s-1", "S-99"]})
    await auto_expected.draft_for_live(None, now=NOW)
    assert {tuple(r["evidence"]["rules"]) for r in await _drafts(lab_scratch)} == {("S-1",)}


async def test_no_draft_without_an_approved_spec(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 0 and out["skipped"] and asked == [] and await _drafts(lab_scratch) == []


async def test_codex_turns_and_answered_questions_are_never_drafted(lab_scratch, main_scratch, monkeypatch):
    live = await _old_day(main_scratch, lab_scratch)          # a hand question and a pre-13-Nov Codex turn in the day
    await seed_session(lab_scratch, "scn__q_v0__SD-1__1", tag="q_v0", turn_ids=("t-codex",))
    await runs.sync_sessions(None)                            # a Codex batch run with its own question
    await _spec()
    hand = next(q for q in (await runs.list_questions(member(), live))["questions"])
    await runs.set_expected(member(), hand["code"], expected="Typed by a person.")
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW + timedelta(hours=2))
    assert out["drafted"] == 0 and asked == [], "the hand question has a person's answer; Codex turns are not live"


async def test_the_off_switch(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    asked = _model(monkeypatch)
    monkeypatch.setenv("TRACE_LAB_AUTO_EXPECTED", "false")
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 0 and out["skipped"] and asked == []


async def test_older_questions_too_and_at_most_a_round(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    # 18 Nov 2026: every live question, however old ("i need expected ans on each question"); the round still caps it.
    assert (await auto_expected.draft_for_live(None, now=NOW + timedelta(days=60), limit=2))["drafted"] == 2
    assert (await auto_expected.draft_for_live(None, now=NOW))["drafted"] == 1, "the rest next round"


async def test_a_failed_model_call_stores_nothing_and_is_tried_again(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch, fail=True)
    assert await auto_expected.draft_for_live(None, now=NOW) == {"drafted": 0, "failed": 3, "keyed": 0, "replaced": 0, "redrafted": 0, "skipped": None}
    assert await _drafts(lab_scratch) == []
    _model(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["drafted"] == 3


async def test_a_question_is_drafted_once(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    asked = _model(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["drafted"] == 0 and asked == []


async def test_a_persons_answer_written_meanwhile_wins(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _spec()
    first = (await runs.list_questions(member(), code))["questions"][-1]

    wrote = []

    async def slow(system, prompt, schema, max_tokens):
        if first["question"] in prompt and not wrote:
            wrote.append(1)
            await runs.set_expected(member(), first["code"], expected="A person got there first.")
        return {"expected": "Drafted.", "rules": ["S-1"]}
    monkeypatch.setattr(agent, "_ask", slow)
    out = await auto_expected.draft_for_live(None, now=NOW)
    rows = [r for r in await _drafts(lab_scratch) if r["qid"] == first["id"]]
    assert [r["source"] for r in rows] == ["manual"]


async def test_a_company_round_drafts_only_its_own(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    assert (await auto_expected.draft_for_live(OTHER_ORG, now=NOW))["drafted"] == 0
    assert (await auto_expected.draft_for_live(ORG, now=NOW))["drafted"] == 3


# ── A turn's expected answer on the session page: live turns only ──────────────────────────────────────────────────

async def _turn_of(code, n=-1):
    q = (await runs.list_questions(member(), code))["questions"][n]
    return q, q["turn_ids"][0]


async def test_a_live_turn_shows_its_draft_until_a_person_accepts_it(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    version = await _spec()
    _model(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    q, turn = await _turn_of(code)
    got = await runs.turn_expected(member(), turn)
    assert got["live"] is True and got["question"] == q["code"] and got["draft"] is True and got["decision"] is None
    assert got["spec_version"] == version and got["rules"] == ["S-1", "S-4"]
    assert got["expected"]["expected"].startswith("Lists each overdue") and got["from_draft"] is False
    # Accept: the person saves the draft as their own version, citing its rule (what the session page sends).
    await runs.set_expected(member(), q["code"], expected=got["expected"]["expected"],
                            evidence={"spec_rule": "S-1", "drafted_from": "SPEC.md", "spec_version": version})
    took = await runs.turn_expected(member(), turn)
    assert took["draft"] is False and took["from_draft"] is True and took["rules"] == ["S-1"]
    await runs.set_expected(member(), q["code"], expected="A person's version.")
    mine = await runs.turn_expected(member(), turn)
    assert mine["draft"] is False and mine["from_draft"] is False and mine["expected"]["expected"] == "A person's version."


async def test_drafts_wait_on_the_session_page_not_in_the_loop(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    assert (await loop.proposals(member()))["expected"] == [], "one per live question would flood the Loop"


async def test_a_codex_turn_is_not_live(lab_scratch, main_scratch):
    await seed_session(lab_scratch, "scn__q_v0__SD-1__1", tag="q_v0", turn_ids=("t-codex",))
    await runs.sync_sessions(None)
    got = await runs.turn_expected(member(), "t-codex")
    assert got["live"] is False


async def test_a_turn_with_no_question_and_another_companys_turn(lab_scratch, main_scratch):
    code = await _live_run(main_scratch)
    assert (await runs.turn_expected(member(), "turn-nobody"))["question"] is None
    _q, turn = await _turn_of(code)
    with pytest.raises(NotFound):
        await runs.turn_expected(member(org=OTHER_ORG, all_companies=False, role="admin"), turn)


# ── In the sync: after the live questions, Sync now and the automatic round draft their expected answers ──────────

async def test_sync_now_drafts_expected_answers_after_the_live_questions(lab_scratch, main_scratch, monkeypatch):
    from test_trace_lab_run_sessions_sql import _main

    calls = []

    async def fake(org, *, now=None, limit=auto_expected.PER_ROUND):
        calls.append(org)
        return {"drafted": 2, "failed": 1, "skipped": None}
    monkeypatch.setattr(auto_expected, "draft_for_live", fake)
    await _main(main_scratch, "turn-a1", "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70")
    out = await runs.sync_production(member(org=ORG, all_companies=False, role="admin"), now=NOW)
    assert out["new_questions"] == 1 and out["drafted_expected"] == 2 and calls == [ORG]
    assert "expected" not in out["failed"]


async def test_a_failed_draft_step_never_stops_the_sync(lab_scratch, main_scratch, monkeypatch):
    from test_trace_lab_run_sessions_sql import _main

    async def boom(org, *, now=None, limit=auto_expected.PER_ROUND):
        raise RuntimeError("model down")
    monkeypatch.setattr(auto_expected, "draft_for_live", boom)
    await _main(main_scratch, "turn-a1", "3f2c1a6e-5b7d-4c8e-9a1f-2b3c4d5e6f70")
    out = await runs.sync_production(member(org=ORG, all_companies=False, role="admin"), now=NOW)
    assert out["new_questions"] == 1 and out["drafted_expected"] == 0 and out["failed"]["expected"] == "model down"


async def test_the_automatic_round_drafts_them_and_says_so(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import autosync

    async def fake(org, *, now=None, limit=auto_expected.PER_ROUND):
        return {"drafted": 3, "failed": 0, "skipped": None}
    monkeypatch.setattr(auto_expected, "draft_for_live", fake)
    status = await autosync.tick()
    assert status["last_new_expected"] == 3 and status["expected_ok"] is True and status["expected_error"] is None

    async def boom(org, *, now=None, limit=auto_expected.PER_ROUND):
        raise RuntimeError("model down")
    monkeypatch.setattr(auto_expected, "draft_for_live", boom)
    status = await autosync.tick()
    assert status["expected_ok"] is False and "model down" in status["expected_error"] and status["last_ok"] is not None


async def test_the_turn_expected_route(lab_scratch, main_scratch):
    import json

    from trace_lab import routes_runs

    code = await _live_run(main_scratch)
    _q, turn = await _turn_of(code)
    got = await routes_runs.lab_turn_expected(turn, m=member())
    assert got["ok"] is True and got["live"] is True and got["expected"] is None
    json.dumps(got)


# ── The answer key first (16 Nov 2026): a question it knows gets values read from the records, each with its receipt ──

def _key_reads(monkeypatch, *, raise_=None, found=True):
    from trace_lab import answer_key

    seen = []

    async def fake(key, params, org):
        seen.append((key.id, params, org))
        if raise_:
            raise raise_
        receipts = [{"id": "q1", "label": "count", "sql": "SELECT count(*) FROM vendors", "params": {}, "row_count": 1,
                     "rows": [{"n": 2}], "psql": "BEGIN READ ONLY; ..."}]
        if not found:
            return {"found": False, "unresolved": ["Nowhere House"], "facts": [], "receipts": receipts, "read_at": "t"}
        return {"found": True, "facts": [{"id": "f1", "label": "Blocked vendors", "value": 2, "receipt": "q1"},
                                         {"id": "f2", "label": "", "value": "Pennard Fire Services", "receipt": "q1"},
                                         {"id": "f3", "label": "", "value": "Ostley Power", "receipt": "q1"}],
                "receipts": receipts, "read_at": "2026-11-16T09:00:00+00:00", "plan": {"focus": {"concept": "Vendor"}}}
    monkeypatch.setattr(answer_key, "run", fake)
    return seen


UNKEYED = "Summarise the energy use at B-301 last month."


async def _mixed_run(main) -> str:
    """One question the answer key knows (blocked vendors) and two it does not, so the SPEC draft still has work.
    (The default seed asks about certificates expiring in 30 days, which the key now answers.)"""
    await _main(main, "turn-a1", CHAT_A, question=UNKEYED)
    await _main(main, "turn-a2", CHAT_A, at=T0 + timedelta(minutes=2), question=UNKEYED)
    await _main(main, "turn-b1", CHAT_B, at=T0 + timedelta(minutes=5), question="Which vendors are blocked?")
    await runs.sync_live(None, now=NOW)
    return (await runs.list_runs(member(), kind="production"))["runs"][0]["code"]


async def test_a_question_the_key_knows_gets_values_from_the_records(lab_scratch, main_scratch, monkeypatch):
    code = await _mixed_run(main_scratch)
    await _spec()
    asked = _model(monkeypatch)
    seen = _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 1
    assert seen == [("vendors_blocked", {}, ORG)]
    assert not any("vendors are blocked" in a["prompt"].lower() for a in asked), "no model call for a keyed question"
    q = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] == "Which vendors are blocked?")
    got = await runs.turn_expected(member(), q["turn_ids"][0])
    assert got["kind"] == "answer_key" and got["draft"] is True
    assert got["expected"]["expected"] == "2 vendors are blocked: Pennard Fire Services, Ostley Power."
    ev = got["expected"]["evidence"]
    assert ev["drafted_from"] == "answer_key" and ev["key"] == "vendors_blocked" and ev["receipts"][0]["sql"].startswith("SELECT")
    assert [f["value"] for f in ev["facts"]] == [2, "Pennard Fire Services", "Ostley Power"]


async def test_when_the_key_cannot_read_the_spec_draft_is_used(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 0
    assert {r["evidence"]["drafted_from"] for r in await _drafts(lab_scratch)} == {"SPEC.md"}


async def test_a_name_the_records_do_not_hold_falls_back_to_the_spec_draft(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, found=False)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 0 and out["drafted"] == 3


async def test_the_key_works_without_an_approved_spec(lab_scratch, main_scratch, monkeypatch):
    await _mixed_run(main_scratch)
    asked = _model(monkeypatch)
    _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 1 and out["drafted"] == 1 and asked == [], "the other two wait for a spec"


async def test_run_again_reads_the_same_key_for_the_same_company_and_stores_nothing(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key, routes_runs
    from trace_lab.review_errors import Invalid

    code = await _mixed_run(main_scratch)
    seen = _key_reads(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    q = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] == "Which vendors are blocked?")
    before = await _drafts(lab_scratch)
    got = await routes_runs.lab_turn_expected_recheck(q["turn_ids"][0], m=member())
    assert got["ok"] is True and [f["value"] for f in got["facts"]][0] == 2 and got["receipts"] and got["read_at"]
    assert got["sentence"].startswith("2 vendors are blocked")
    assert seen[-1] == ("vendors_blocked", {}, ORG) and await _drafts(lab_scratch) == before
    with pytest.raises(NotFound):
        await answer_key.recheck(member(org=OTHER_ORG, all_companies=False, role="admin"), q["turn_ids"][0])
    other = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] != "Which vendors are blocked?")
    with pytest.raises(Invalid):
        await answer_key.recheck(member(), other["turn_ids"][0])


async def test_the_orchestrators_query_can_be_corrected_and_read_again(lab_scratch, main_scratch, monkeypatch):
    import json as _json

    from test_trace_lab_answer_key import PLAN, _onto
    from trace_lab import answer_key, eval_store, routes_runs
    from trace_lab.review_errors import Invalid

    code = await _live_run(main_scratch)
    q = (await runs.list_questions(member(), code))["questions"][-1]
    turn = q["turn_ids"][0]

    async def tree(m, trace_id):
        return {"nodes": [{"output": {"content": _json.dumps(PLAN)}}]}
    monkeypatch.setattr(eval_store, "trace_tree_of", tree)

    async def onto(_oq):
        return _onto()
    monkeypatch.setattr(answer_key, "_ontology", onto)
    ran = []

    async def run_plan(plan, org, facts_of):
        ran.append((plan, org))
        return {"found": True, "facts": [{"id": "f1", "label": "Compliance certificates found", "value": 3, "receipt": "q1"}],
                "receipts": [{"id": "q1", "label": "count", "sql": "SELECT 1"}], "read_at": "t", "plan": plan}
    monkeypatch.setattr(answer_key, "_run_plan", run_plan)
    monkeypatch.setattr("trace_lab.db._main", object())
    import sys
    import types
    monkeypatch.setitem(sys.modules, "src.agents.ontology_qa", types.SimpleNamespace())

    shown = await routes_runs.lab_turn_expected_query(turn, m=member())
    assert shown["ok"] is True and shown["plans"][0]["fields"]["dates"][0]["value"] == "next_30_days"
    body = routes_runs.QueryChange(index=0, states=["expiring_soon"], building="",
                                   dates=[{"attribute": "expiry_date", "op": "within", "value": "next_60_days"}])
    got = await routes_runs.lab_turn_expected_try(turn, body, m=member())
    assert got["ok"] is True and got["sentence"] == "3 compliance certificates." and ran[-1][1] == ORG
    assert "expiry_date: within next_30_days → within next_60_days" in got["changes"]
    assert ran[-1][0]["focus"]["filters"][0]["value"] == "next_60_days"
    with pytest.raises(Invalid):
        await answer_key.try_query(member(), turn, index=0, fields={"states": ["nonsense"]})
    with pytest.raises(NotFound):
        await answer_key.try_query(member(org=OTHER_ORG, all_companies=False, role="admin"), turn, index=0, fields={})


# ── A SPEC.md draft the answer key can now answer gets the values (16 Nov 2026: R59 "lapse in the next 30 days" was
#    drafted from SPEC.md before the key knew "lapse", and drafting only ever filled questions with no answer) ──

async def test_an_untouched_spec_draft_is_replaced_by_the_keys_values_once_the_key_knows_the_question(
        lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    code = await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    first = await auto_expected.draft_for_live(None, now=NOW)
    assert first["drafted"] == 3 and first["keyed"] == 0
    asked = _model(monkeypatch)
    _key_reads(monkeypatch)
    again = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert again["replaced"] == 3 and again["keyed"] == 3 and asked == [], "no new model call to replace a draft"
    for q in (await runs.list_questions(member(), code))["questions"]:
        got = await runs.turn_expected(member(), q["turn_ids"][0])
        assert got["kind"] == "answer_key" and got["draft"] is True
    rows = await _drafts(lab_scratch)
    assert [r["evidence"]["drafted_from"] for r in rows] == ["SPEC.md"] * 3 + ["answer_key"] * 3, "the SPEC draft is kept as history"
    third = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=2))
    assert third["replaced"] == 0 and third["drafted"] == 0 and len(await _drafts(lab_scratch)) == 6


async def test_a_persons_answer_is_never_replaced_by_the_key(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    code = await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    await auto_expected.draft_for_live(None, now=NOW)
    q = (await runs.list_questions(member(), code))["questions"][0]
    await runs.set_expected(member(), q["code"], expected="A person's version.")
    _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert out["replaced"] == 2
    got = await runs.turn_expected(member(), q["turn_ids"][0])
    assert got["expected"]["expected"] == "A person's version." and got["kind"] == "person"


async def test_a_spec_draft_the_key_cannot_read_stays_as_it_is(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    await auto_expected.draft_for_live(None, now=NOW)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["replaced"] == 0 and len(await _drafts(lab_scratch)) == 3


# ── Review fixes before commit (16 Nov 2026) ──

async def test_a_persons_save_waits_for_the_drafting_round_and_wins(lab_scratch, main_scratch, monkeypatch):
    import asyncio

    code = await _live_run(main_scratch)
    q = (await runs.list_questions(member(), code))["questions"][0]
    async with lab_scratch.begin() as hold:
        await hold.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
        with pytest.raises(asyncio.TimeoutError):          # the save queues behind a draft being written
            await asyncio.wait_for(runs.set_expected(member(), q["code"], expected="Mine."), timeout=1.5)
    await runs.set_expected(member(), q["code"], expected="Mine.")
    async with lab_scratch.begin() as c:
        await c.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": runs.EXPECTED_LOCK})
        await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected="Later.", expected_id=None,
                                    evidence=None, source="coding_agent")
    got = await runs.turn_expected(member(), q["turn_ids"][0])
    assert got["expected"]["expected"] == "Later.", "versions are ordered by when they were written, not when the transaction began"


async def test_a_key_that_cannot_read_rests_before_it_is_tried_again(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    await _live_run(main_scratch)
    seen = _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    await auto_expected.draft_for_live(None, now=NOW)
    tried = len(seen)
    assert tried >= 1
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert len(seen) == tried, "not read again a minute later"
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert len(seen) > tried, "tried again after the rest"


async def test_correcting_a_query_before_the_map_is_ready_says_so(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key, routes_runs
    from trace_lab.review_errors import Invalid

    async def turn_plans(m, turn_id):
        return {"company": ORG}, [{"focus": {"concept": "WorkOrder", "states": ["open"]}}], "hoistra"

    async def not_ready(oq):
        raise answer_key.NotReady("no map yet")

    monkeypatch.setattr(answer_key, "_turn_plans", turn_plans)
    monkeypatch.setattr(answer_key, "_ontology", not_ready)
    with pytest.raises(Invalid) as e:
        await answer_key.try_query(member(), "turn-x", index=0, fields={"states": ["open"]})
    assert e.value.reason == "not_ready"


async def test_values_read_from_the_records_wait_on_the_session_page_too(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 3
    assert (await loop.proposals(member()))["expected"] == [], "accepted on the session page, never in the Loop"


# ── Every live question gets an expected answer (18 Nov 2026, Hussain: "i need expected ans on each question"). The old
#    prompt let the model write "No rule of the specification applies to this question." as the expected answer; that
#    placeholder then counted as an answer, so the question was never drafted again, not even after a new SPEC.md. ──

async def _seed_lab_review(lab, subject_id):
    async with lab.begin() as c:
        await c.execute(text(
            "INSERT INTO tracelab.lab_reviews (subject_kind, subject_id, decision, reviewer_id, reviewer_email)"
            " VALUES ('expected_answer', CAST(:s AS uuid), 'approved', gen_random_uuid(), 'boss@example.com')"),
            {"s": subject_id})


PLACEHOLDER = "No rule of the specification applies to this question."


async def _seed_placeholders(lab, code, version, only=None):
    """What the old prompt stored for a question: the placeholder, no rules, no draft version (the table is append-only,
    so this is the question's first and only expected answer)."""
    qs = (await runs.list_questions(member(), code))["questions"]
    by_age = sorted(qs, key=lambda q: q["asked_at"])
    pick = {"oldest": [by_age[0]], "newest": [by_age[-1]]}
    async with lab.begin() as c:
        for q in qs if only is None else pick[only]:
            await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected=PLACEHOLDER,
                                        expected_id=None, source="coding_agent",
                                        evidence={"drafted_from": "SPEC.md", "spec_version": version, "rules": []})
    return qs


async def _newest(lab):
    async with lab.connect() as c:
        return {r["qid"]: dict(r) for r in (await c.execute(text(
            "SELECT DISTINCT ON (e.question_id) e.question_id::text AS qid, e.id::text AS id, e.expected, e.evidence,"
            " e.source FROM tracelab.lab_expected_answers e ORDER BY e.question_id, e.created_at DESC, e.id DESC")
        )).mappings().all()}


async def test_the_model_is_told_to_always_write_what_a_correct_answer_contains(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    asked = _model(monkeypatch, reply={"expected": "States the floors of building ABC from the register, or says no "
                                                   "building ABC is on record.", "rules": []})
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3
    system = asked[0]["system"]
    assert "say that no rule of the specification applies" not in system, "the placeholder is no longer asked for"
    assert "never write that no rule applies" in system.lower()
    row = (await _drafts(lab_scratch))[0]
    assert row["evidence"]["rules"] == [] and row["evidence"]["draft"] == auto_expected.DRAFT_VERSION
    assert row["expected"].startswith("States the floors"), "a draft that cites no rule still says what the answer holds"


async def test_an_old_placeholder_is_drafted_again(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _seed_placeholders(lab_scratch, code, await _spec())
    old = await _newest(lab_scratch)
    asked = _model(monkeypatch, reply={"expected": "Lists each overdue certificate with its days overdue.", "rules": ["S-1"]})
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 3 and out["drafted"] == 0 and len(asked) == 3
    new = await _newest(lab_scratch)
    for qid, row in new.items():
        assert row["expected"] == "Lists each overdue certificate with its days overdue."
        assert row["source"] == "coding_agent" and row["evidence"]["replaced_draft"] == old[qid]["id"]
        assert row["evidence"]["draft"] == auto_expected.DRAFT_VERSION
    assert len(await _drafts(lab_scratch)) == 6, "the placeholder stays as history"
    again = _model(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["redrafted"] == 0 and again == [], "a current draft stays"


async def test_a_draft_from_an_older_spec_is_drafted_again_after_a_new_version(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    first = await _spec()
    _model(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    second = await _spec()
    assert second != first
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 3 and len(asked) == 3
    assert {r["evidence"]["spec_version"] for r in (await _newest(lab_scratch)).values()} == {second}


async def test_a_persons_accepted_or_reviewed_answer_is_never_drafted_again(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    qs = await _seed_placeholders(lab_scratch, code, await _spec())
    await runs.set_expected(member(), qs[0]["code"], expected="A person's version.")
    newest = await _newest(lab_scratch)
    await _seed_lab_review(lab_scratch, newest[qs[1]["id"]]["id"])
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 1 and len(asked) == 1, "only the untouched placeholder"
    after = await _newest(lab_scratch)
    assert after[qs[0]["id"]]["expected"] == "A person's version."
    assert after[qs[1]["id"]]["id"] == newest[qs[1]["id"]]["id"]


async def test_a_person_writing_meanwhile_beats_the_redraft(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    first = (await _seed_placeholders(lab_scratch, code, await _spec()))[-1]
    wrote = []

    async def slow(system, prompt, schema, max_tokens):
        if first["question"] in prompt and not wrote:
            wrote.append(1)
            await runs.set_expected(member(), first["code"], expected="A person got there first.")
        return {"expected": "Drafted again.", "rules": ["S-1"]}
    monkeypatch.setattr(agent, "_ask", slow)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 2
    assert (await _newest(lab_scratch))[first["id"]]["expected"] == "A person got there first."


async def test_redrafts_share_the_rounds_budget_with_new_questions(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _seed_placeholders(lab_scratch, code, await _spec(), only="oldest")  # a placeholder; two newer still new
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW, limit=2)
    assert out["drafted"] == 2 and out["redrafted"] == 0 and len(asked) == 2, "the two newer questions first"
    out = await auto_expected.draft_for_live(None, now=NOW, limit=2)
    assert out["drafted"] == 0 and out["redrafted"] == 1


async def test_a_failed_redraft_keeps_the_old_draft_and_is_tried_again(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _seed_placeholders(lab_scratch, code, await _spec())
    _model(monkeypatch, fail=True)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 0 and out["failed"] == 3 and len(await _drafts(lab_scratch)) == 3
    _model(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["redrafted"] == 3


async def test_the_automatic_round_counts_redrafts_too(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import autosync

    async def fake(org, *, now=None, limit=auto_expected.PER_ROUND):
        return {"drafted": 1, "redrafted": 2, "failed": 0, "skipped": None}
    monkeypatch.setattr(auto_expected, "draft_for_live", fake)
    status = await autosync.tick()
    assert status["last_new_expected"] == 3


async def test_the_newest_question_goes_first_whether_new_or_a_redraft(lab_scratch, main_scratch, monkeypatch):
    """A placeholder on the question people just asked is redrafted before a backlog of older new questions."""
    code = await _live_run(main_scratch)
    qs = await _seed_placeholders(lab_scratch, code, await _spec(), only="newest")   # the newest has a placeholder
    newest = max(qs, key=lambda q: q["asked_at"])
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW, limit=1)
    assert out["redrafted"] == 1 and out["drafted"] == 0 and newest["question"] in asked[0]["prompt"]
