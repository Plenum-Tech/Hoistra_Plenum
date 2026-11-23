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

from eval_fixtures import ANSWER, ORG, OTHER_ORG, T0, UID, member, seed_session
from test_trace_lab_records_queries import db_span, llm, tool
from test_trace_lab_records_values import ONTO, PLAN_LIST_WOS, run_with_records
from test_trace_lab_run_sessions_sql import CHAT_A, CHAT_B, NOW, _live_run, _main, _old_day
from tl_scratch import lab_scratch, main_scratch, requires_scratch  # noqa: F401
from trace_lab import agent, auto_expected, loop, records_queries, records_run, records_values, runs
from trace_lab.review_errors import NotFound

pytestmark = requires_scratch

_REAL_QUERIES_FOR = records_queries.queries_for
_REAL_ASKER_SCOPE = auto_expected.asker_scope

RULES = [{"code": "S-1", "title": "overdue_reported_as_none",
          "text": "When the register holds overdue items, list each one with its days overdue; never report none."},
         {"code": "S-4", "title": "list_answer_as_paragraph", "text": "A list question is answered as a list."}]


@pytest.fixture(autouse=True)
def _no_rests(monkeypatch):
    monkeypatch.setattr(auto_expected, "_resting", {})
    monkeypatch.setattr(auto_expected, "_settled", {})
    monkeypatch.setattr(auto_expected, "_looks", {})
    monkeypatch.setattr(auto_expected, "_held", {}, raising=False)


@pytest.fixture(autouse=True)
def _company_asker(monkeypatch):
    """By default the asker's access is not the point of a test: the read is for the whole company. The tests about the
    asker's access set one (_asker) or call _REAL_ASKER_SCOPE."""
    async def company(org, user_id):
        return {"scope": "company", "building_ids": None, "email": None}
    monkeypatch.setattr(auto_expected, "asker_scope", company)


@pytest.fixture(autouse=True)
def _no_records(monkeypatch):
    """19 Nov 2026: a draft is the exact values of a records query. By default no question has one (so the drafts below
    are the SPEC.md ones); the tests that read values set queries up (_queries, _records, _key_reads)."""
    async def none(nodes, question, *, propose):
        return {"queries": [], "none": "not_about_records"}
    monkeypatch.setattr(records_queries, "queries_for", none)


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
                                    "draft": auto_expected.DRAFT_VERSION, "no_query": "no_trace", "tries": 0,
                                    "last_tried": NOW.isoformat(), "records_gate": auto_expected.RECORDS_GATE}
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

def _vendors_run(plan):
    """What reading a plan about vendors gives: two blocked vendors, as records_run.run_query returns it."""
    records = [{"key": "v1", "attributes": {"vendor_name": "Pennard Fire Services"}, "context": {}, "related": {}},
               {"key": "v2", "attributes": {"vendor_name": "Ostley Power"}, "context": {}, "related": {}}]
    receipts = [{"id": "q1", "label": "count", "sql": "SELECT count(*) FROM vendors", "params": {}, "row_count": 1,
                 "rows": [{"n": 2}], "psql": "BEGIN READ ONLY; ..."},
                {"id": "q2", "label": "focus Vendor", "sql": "SELECT * FROM vendors", "params": {}, "row_count": 2,
                 "rows": [], "psql": "BEGIN READ ONLY; ..."}]
    return {"found": True, "unresolved": [], "plan": plan, "notes": [], "receipts": receipts,
            "read_at": "2026-11-16T09:00:00+00:00", "pending": None,
            "result": {"kind": "records", "focus": "Vendor", "total": 2, "records": records, "paths": []}}


def _key_reads(monkeypatch, *, raise_=None, found=True):
    """The answer key's fixed queries are read through records_run.run_query (19 Nov 2026): the real queries_for (its own
    question shapes) and a faked read. Returns the (focus, company) of every read."""
    seen = []

    async def load_map():
        return ONTO

    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        seen.append((plan["focus"]["concept"], org))
        if raise_:
            raise raise_
        if not found:
            return {"found": False, "unresolved": ["Nowhere House"], "plan": plan, "notes": [], "receipts": [],
                    "read_at": "t", "result": None, "pending": None}
        return _vendors_run(plan)
    monkeypatch.setattr(records_queries, "queries_for", _REAL_QUERIES_FOR)
    monkeypatch.setattr(records_run, "load_map", load_map)
    monkeypatch.setattr(records_run, "run_query", run_query)
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
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    await _spec()
    asked = _model(monkeypatch)
    seen = _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 1
    assert seen == [("Vendor", ORG)]
    assert not any("vendors are blocked" in a["prompt"].lower() for a in asked), "no model call for a keyed question"
    q = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] == "Which vendors are blocked?")
    got = await runs.turn_expected(member(), q["turn_ids"][0])
    assert got["kind"] == "records" and got["draft"] is True
    assert got["expected"]["expected"] == "Vendor records found: 2. Vendors: Pennard Fire Services, Ostley Power."
    ev = got["expected"]["evidence"]
    assert ev["drafted_from"] == "records" and ev["queries"][0]["source"] == "answer_key"
    assert ev["queries"][0]["receipts"][0]["sql"].startswith("SELECT")
    assert [f["value"] for f in ev["queries"][0]["facts"]] == [2, "Pennard Fire Services", "Ostley Power"]


async def test_when_the_key_cannot_read_the_spec_draft_is_used(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 0
    assert {r["evidence"]["drafted_from"] for r in await _drafts(lab_scratch)} == {"SPEC.md"}


async def test_a_name_the_records_do_not_hold_is_a_value_not_a_fallback(lab_scratch, main_scratch, monkeypatch):
    """19 Nov 2026: "Not on record: Nowhere House" is the exact answer (never a zero), so it is the draft."""
    await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    await _spec()
    asked = _model(monkeypatch)
    _key_reads(monkeypatch, found=False)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 3 and out["drafted"] == 3 and asked == []
    row = next(iter((await _newest(lab_scratch)).values()))
    assert row["expected"] == "Not on record: Nowhere House." and row["evidence"]["drafted_from"] == "records"
    block = row["evidence"]["queries"][0]
    assert block["found"] is False and block["unresolved"] == ["Nowhere House"]
    assert [(f["kind"], f["value"]) for f in block["facts"]] == [("missing", "Nowhere House")]


async def test_the_key_works_without_an_approved_spec(lab_scratch, main_scratch, monkeypatch):
    await _mixed_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    asked = _model(monkeypatch)
    _key_reads(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 1 and out["drafted"] == 1 and asked == [], "the other two wait for a spec"


async def test_run_again_reads_the_same_key_for_the_same_company_and_stores_nothing(lab_scratch, main_scratch, monkeypatch):
    """A draft the answer key itself wrote (drafted_from "answer_key", before 19 Nov 2026) is read again as before; the
    drafting round now writes values read through records_run instead (the re-run follows them in a later task)."""
    from trace_lab import answer_key, routes_runs
    from trace_lab.review_errors import Invalid

    seen = []

    async def fake(key, params, org, *, question=""):
        seen.append((key.id, params, org, question))
        receipts = [{"id": "q1", "label": "count", "sql": "SELECT count(*) FROM vendors", "params": {}, "row_count": 1,
                     "rows": [{"n": 2}], "psql": "BEGIN READ ONLY; ..."}]
        return {"found": True, "facts": [{"id": "f1", "label": "Blocked vendors", "value": 2, "receipt": "q1"},
                                         {"id": "f2", "label": "", "value": "Pennard Fire Services", "receipt": "q1"},
                                         {"id": "f3", "label": "", "value": "Ostley Power", "receipt": "q1"}],
                "receipts": receipts, "read_at": "2026-11-16T09:00:00+00:00", "plan": {"focus": {"concept": "Vendor"}}}
    monkeypatch.setattr(answer_key, "run", fake)
    code = await _mixed_run(main_scratch)
    q = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] == "Which vendors are blocked?")
    async with lab_scratch.begin() as c:
        await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], source="coding_agent", expected_id=None,
                                    expected="2 vendors are blocked: Pennard Fire Services, Ostley Power.",
                                    evidence={"drafted_from": "answer_key", "key": "vendors_blocked", "params": {}})
    before = await _drafts(lab_scratch)
    got = await routes_runs.lab_turn_expected_recheck(q["turn_ids"][0], index=0, m=member())
    assert got["ok"] is True and [f["value"] for f in got["facts"]][0] == 2 and got["receipts"] and got["read_at"]
    assert got["sentence"].startswith("2 vendors are blocked") and got["notes"] == []
    assert seen[-1] == ("vendors_blocked", {}, ORG, "Which vendors are blocked?") and await _drafts(lab_scratch) == before
    with pytest.raises(NotFound):
        await answer_key.recheck(member(org=OTHER_ORG, all_companies=False, role="admin"), q["turn_ids"][0])
    other = next(x for x in (await runs.list_questions(member(), code))["questions"] if x["question"] != "Which vendors are blocked?")
    with pytest.raises(Invalid):
        await answer_key.recheck(member(), other["turn_ids"][0])


# ── Run again now and the query editor, through the routes (19 Nov 2026, Hussain and Bala). A drafted turn's stored query is
#    read again; the editor's routes show the stored query as run with Hoistra's map, and run a person's own plan beside
#    Hoistra's. Another company's turn is never found by any of them. ──

async def _drafted(lab, main, monkeypatch, *, plan=None, asker=None):
    """A live day whose three questions are drafted as values read from `plan` (Floor and Building by default), with the
    real shape of Hoistra's map (records_form's) for the routes that check a plan. Returns (code, newest turn, reads)."""
    from test_trace_lab_records_form import R62, _onto

    code = await _live_run(main)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [_hoistra(q, plan=plan or R62)], "none": None})
    reads = _records(monkeypatch)

    async def load_map():
        return _onto()
    monkeypatch.setattr(records_run, "load_map", load_map)
    if asker is not None:
        _asker(monkeypatch, asker)
    await auto_expected.draft_for_live(None, now=NOW)
    _q, turn = await _turn_of(code)
    return code, turn, reads


async def test_run_again_reads_a_drafted_query_afresh_for_the_turns_company_and_stores_nothing(lab_scratch, main_scratch, monkeypatch):
    from test_trace_lab_records_form import R62

    from trace_lab import answer_key, routes_runs
    from trace_lab.review_errors import Invalid

    _code, turn, reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    asked = _asker(monkeypatch)
    drafts, drafting_reads = await _drafts(lab_scratch), len(reads)
    got = await routes_runs.lab_turn_expected_recheck(turn, index=0, m=member())
    assert got["ok"] is True and got["found"] is True and got["facts"][0]["value"] == 25 and got["receipts"] and got["read_at"]
    assert got["sentence"].startswith("Floor records found: 25.") and got["notes"] == [] and got["unresolved"] == []
    assert len(reads) == drafting_reads + 1 and reads[-1]["plan"] == R62 and reads[-1]["org"] == ORG
    assert reads[-1]["mode"] == "hoistra" and reads[-1]["question"], "the stored query, the way Hoistra runs one"
    assert reads[-1]["scope"] is None and asked == [], "it was read for the whole company, so it is read again as that"
    assert got["scope"] == "company" and await _drafts(lab_scratch) == drafts, "nothing is stored"
    with pytest.raises(Invalid) as e:
        await answer_key.recheck(member(), turn, index=7)
    assert e.value.reason == "no_query"
    with pytest.raises(NotFound):
        await answer_key.recheck(member(org=OTHER_ORG, all_companies=False, role="admin"), turn, index=0)


async def test_run_again_on_a_query_read_as_the_asker_is_read_with_their_access(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import routes_runs

    who = {"scope": "asker", "building_ids": ["b1"], "email": "asker@example.com"}
    _code, turn, reads = await _drafted(lab_scratch, main_scratch, monkeypatch, asker=who)
    asked = _asker(monkeypatch, who)
    got = await routes_runs.lab_turn_expected_recheck(turn, index=0, m=member())
    assert asked == [(ORG, UID)], "the person on the turn, in the question's company"
    assert reads[-1]["scope"] == {"building_ids": ["b1"], "email": "asker@example.com"}
    assert got["scope"] == "asker" and got["scope_email"] == "asker@example.com"


async def test_the_editor_shows_the_stored_query_and_the_map_and_who_may_run_it(lab_scratch, main_scratch, monkeypatch):
    from test_trace_lab_records_form import R62

    from trace_lab import routes_runs

    _code, turn, _reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    got = await routes_runs.lab_turn_expected_query(turn, m=member())
    assert got["ok"] is True and got["ready"] is True and got["can_run"] is True and got["none"] is None
    assert [(x["index"], x["source"], x["plan"]) for x in got["queries"]] == [(0, "hoistra", R62)]
    assert got["queries"][0]["source_words"] and got["queries"][0]["question"]
    assert {c["name"] for c in got["map"]["concepts"]} >= {"Floor", "Building"} and got["map"]["ops"] and got["map"]["periods"]
    viewer = member(org=ORG, role="reviewer", all_companies=False, manages=False)
    assert (await routes_runs.lab_turn_expected_query(turn, m=viewer))["can_run"] is False


async def test_a_person_runs_their_own_plan_beside_hoistras(lab_scratch, main_scratch, monkeypatch):
    import json as _json

    from test_trace_lab_records_form import R62

    from trace_lab import routes_runs

    _code, turn, reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    new = _json.loads(_json.dumps(R62))
    new["focus"]["concept"], new["constraints"] = "Building", []
    drafting, drafts = len(reads), await _drafts(lab_scratch)
    got = await routes_runs.lab_turn_expected_try(turn, routes_runs.QueryRun(index=0, plan=new), m=member())
    assert got["ok"] is True and got["changes"] == ["What: Floor records → Building records", "Limited to: Building ABC removed"]
    assert got["before"]["found"] is True and got["after"]["found"] is True and got["before"]["receipts"] and got["after"]["facts"]
    assert [(r["mode"], r["plan"]["focus"]["concept"], r["org"]) for r in reads[drafting:]] == [
        ("hoistra", "Floor", ORG), ("as_written", "Building", ORG)]
    assert await _drafts(lab_scratch) == drafts, "nothing is stored: the page saves it when the person says so"


async def test_a_plan_the_map_refuses_is_a_422_in_words_through_the_route(lab_scratch, main_scratch, monkeypatch):
    from fastapi import HTTPException
    from test_trace_lab_records_form import _node, _plan

    from trace_lab import routes_runs

    _code, turn, reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    drafting = len(reads)
    with pytest.raises(HTTPException) as e:
        await routes_runs.lab_turn_expected_try(turn, routes_runs.QueryRun(index=0, plan=_plan(_node("Flor"))), m=member())
    assert e.value.status_code == 422 and e.value.detail["reason"] == "bad_query" and "'Flor' is not a kind of record" in e.value.detail["error"]
    assert len(reads) == drafting, "nothing is read for a plan that does not fit"


async def test_only_curators_and_the_platform_team_run_a_plan_and_another_company_sees_nothing(lab_scratch, main_scratch, monkeypatch):
    from fastapi import HTTPException
    from test_trace_lab_records_form import R62

    from trace_lab import routes_runs

    _code, turn, reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    drafting = len(reads)
    viewer = member(org=ORG, role="reviewer", all_companies=False, manages=False)
    with pytest.raises(HTTPException) as e:
        await routes_runs.lab_turn_expected_try(turn, routes_runs.QueryRun(index=0, plan=R62), m=viewer)
    assert e.value.status_code == 403
    stranger = member(org=OTHER_ORG, all_companies=False, role="admin")
    for call in (routes_runs.lab_turn_expected_query(turn, m=stranger),
                 routes_runs.lab_turn_expected_try(turn, routes_runs.QueryRun(index=0, plan=R62), m=stranger),
                 routes_runs.lab_turn_expected_recheck(turn, index=0, m=stranger)):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 404, "another company's turn is not found"
    assert len(reads) == drafting, "nothing was read for them"


async def test_a_turn_with_no_draft_shows_the_turns_own_query_from_its_trace(lab_scratch, main_scratch, monkeypatch):
    from test_trace_lab_records_form import R62, _onto

    from trace_lab import routes_runs

    code = await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch, nodes=[tool("t1", "answer_from_records")])
    asked = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [dict(_hoistra(q, plan=R62), tool_id="t1")], "none": None})
    reads = _records(monkeypatch)

    async def load_map():
        return _onto()
    monkeypatch.setattr(records_run, "load_map", load_map)
    _q, turn = await _turn_of(code)
    got = await routes_runs.lab_turn_expected_query(turn, m=member())
    assert [(x["index"], x["plan"]) for x in got["queries"]] == [(0, R62)] and "tool_id" not in got["queries"][0]
    assert [c["propose"] for c in asked] == [False], "showing the editor never asks the planner"
    assert asked[0]["nodes"], "the turn's own trace is read"
    assert [(r["mode"], r["plan"], r["org"]) for r in reads] == [("hoistra", R62, ORG)], \
        "the editor starts from the plan as Hoistra's steps ran it (spec call 2)"


# ── A SPEC.md draft the answer key can now answer gets the values (16 Nov 2026: R59 "lapse in the next 30 days" was
#    drafted from SPEC.md before the key knew "lapse", and drafting only ever filled questions with no answer) ──

async def test_an_untouched_spec_draft_is_replaced_by_the_keys_values_once_the_key_knows_the_question(
        lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    code = await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
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
        assert got["kind"] == "records" and got["draft"] is True
    rows = await _drafts(lab_scratch)
    assert [r["evidence"]["drafted_from"] for r in rows] == ["SPEC.md"] * 3 + ["records"] * 3, "the SPEC draft is kept as history"
    third = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=2))
    assert third["replaced"] == 0 and third["drafted"] == 0 and len(await _drafts(lab_scratch)) == 6


async def test_a_persons_answer_is_never_replaced_by_the_key(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import answer_key

    code = await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
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
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    seen = _key_reads(monkeypatch, raise_=answer_key.NotReady("no map yet"))
    await auto_expected.draft_for_live(None, now=NOW)
    tried = len(seen)
    assert tried >= 1
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert len(seen) == tried, "not read again a minute later"
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert len(seen) > tried, "tried again after the rest"


async def test_values_read_from_the_records_wait_on_the_session_page_too(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
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


async def _newest_rows(lab):
    return list((await _newest(lab)).values())


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


# ── Every live question gets exact values from the records (19 Nov 2026, Hussain: "the expected ans should the exact
#    values and ... i can see how this was derived"): a question with a records query is drafted from the values its query
#    reads, each with its receipt; a question with none keeps the SPEC.md draft and says why ──

def _hoistra(question="How many work orders are open?", *, plan=PLAN_LIST_WOS, tool_id="t1"):
    return {"source": "hoistra", "plan": plan, "question": question, "tool_id": tool_id}


def _queries(monkeypatch, answer=None):
    """Fake which queries a turn gets: answer(nodes, question, propose) -> {"queries", "none"}; by default one Hoistra
    query for every question. Returns the calls."""
    calls = []

    async def fake(nodes, question, *, propose):
        calls.append({"nodes": nodes, "question": question, "propose": propose})
        if answer is not None:
            return answer(nodes, question, propose)
        return {"queries": [_hoistra(question)], "none": None}
    monkeypatch.setattr(records_queries, "queries_for", fake)
    return calls


def _records(monkeypatch, *, run=None, raise_=None, raise_for=None):
    """Fake the faithful re-run (and Hoistra's map): every read returns `run` (25 work orders by default) with the plan it
    was given as the plan as run. Returns the reads."""
    calls = []

    async def load_map():
        return ONTO

    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        calls.append({"plan": plan, "question": question, "org": org, "mode": mode, "scope": scope})
        err = raise_ or (RuntimeError("the records did not answer") if raise_for else None)
        if err is not None and (raise_for is None or raise_for in question):
            raise err
        return dict(run or run_with_records(25), plan=plan)
    monkeypatch.setattr(records_run, "load_map", load_map)
    monkeypatch.setattr(records_run, "run_query", run_query)
    return calls


def _asker(monkeypatch, scope=None):
    """Fake the asker's rebuilt access (default: building B1 and B2 of ASKER). Returns the calls."""
    calls = []

    async def fake(org, user_id):
        calls.append((org, user_id))
        return scope or {"scope": "asker", "building_ids": ["b1", "b2"], "email": "asker@example.com"}
    monkeypatch.setattr(auto_expected, "asker_scope", fake)
    return calls


async def _file_traces(lab, monkeypatch, nodes=None, *, questions=None):
    """File the traces of _live_run's three turns (or of _questions' n turns), with `nodes` as their tree (no data tool by
    default): a turn whose trace is filed is known to be about nothing, rather than not known yet."""
    from trace_lab import eval_store

    if questions is not None:
        await seed_session(lab, CHAT_A, tag="chat", turn_ids=tuple(f"turn-q{i}" for i in range(questions)))
    else:
        await seed_session(lab, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
        await seed_session(lab, CHAT_B, tag="chat", turn_ids=("turn-b1",), at=T0 + timedelta(minutes=5))

    async def nodes_of(conn, trace_id):
        return nodes if nodes is not None else [tool("t1", "write_todos")]
    monkeypatch.setattr(eval_store, "nodes_of", nodes_of)


async def _questions(main, n, *, text_of=lambda i: f"How many work orders are open in block {i}?"):
    """n live questions of one person's day (turn-q0 ... turn-q<n-1>, asked a minute apart in conversation A)."""
    for i in range(n):
        await _main(main, f"turn-q{i}", CHAT_A, at=T0 + timedelta(minutes=i), question=text_of(i))
    out = await runs.sync_live(None, now=NOW)
    assert out["new_questions"] == n
    return (await runs.list_runs(member(), kind="production"))["runs"][0]["code"]


async def test_a_question_with_a_query_gets_values_as_its_draft(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    asked = _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out == {"drafted": 3, "failed": 0, "keyed": 3, "replaced": 0, "redrafted": 0,
                   "skipped": "No version of SPEC.md is approved yet: only questions with a records query are drafted."}
    assert asked == [], "values need no model and no SPEC.md"
    q = (await runs.list_questions(member(), code))["questions"][-1]
    row = (await _newest(lab_scratch))[q["id"]]
    ev = row["evidence"]
    assert row["source"] == "coding_agent" and ev["drafted_from"] == "records" and ev["draft"] == 3
    assert ev["queries"][0]["facts"][0]["value"] == 25 and ev["queries"][0]["source"] == "hoistra"
    assert set(ev["queries"][0]) == {"source", "question", "plan", "found", "unresolved", "notes", "facts", "receipts",
                                     "read_at", "sentence", "scope", "scope_email", "changed"}
    block = ev["queries"][0]
    assert block["found"] is True and block["receipts"] and block["read_at"] == ev["read_at"] == "2026-11-19T09:00:00+00:00"
    assert block["facts"][0]["receipt"] == "q1" and block["facts"][0]["label"] == "Work order records found"
    assert row["expected"] == block["sentence"] and row["expected"].startswith("Work order records found: 25.")
    got = await runs.turn_expected(member(), q["turn_ids"][0])
    assert got["kind"] == "records" and got["draft"] is True and got["from_draft"] is False
    read = next(r for r in reads if r["question"] == block["question"])
    assert read["org"] == ORG and read["mode"] == "hoistra" and read["plan"] == PLAN_LIST_WOS


async def test_the_asker_is_not_known_here_so_the_whole_company_is_read_and_it_says_so(lab_scratch, main_scratch, monkeypatch):
    """The accounts do not know the person on the turn (deterministic): the read is for the whole company."""
    await _live_run(main_scratch)
    await _accounts(main_scratch)
    monkeypatch.setattr(auto_expected, "asker_scope", _REAL_ASKER_SCOPE)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    block = next(iter((await _newest(lab_scratch)).values()))["evidence"]["queries"][0]
    assert block["scope"] == "company" and block["scope_email"] is None
    assert reads[0]["scope"] is None, "no scope: the whole company, as run_query reads it"


async def test_the_read_is_made_with_the_askers_own_access(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    who = _asker(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    assert who and all(call == (ORG, UID) for call in who), "the person on the turn, in the question's company"
    assert all(r["scope"] == {"building_ids": ["b1", "b2"], "email": "asker@example.com"} for r in reads)
    for row in (await _newest(lab_scratch)).values():
        block = row["evidence"]["queries"][0]
        assert block["scope"] == "asker" and block["scope_email"] == "asker@example.com"


async def test_an_administrator_is_read_for_every_building_but_still_as_the_asker(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    _asker(monkeypatch, {"scope": "asker", "building_ids": None, "email": "boss@example.com"})
    await auto_expected.draft_for_live(None, now=NOW)
    assert all(r["scope"] == {"building_ids": None, "email": "boss@example.com"} for r in reads)
    assert next(iter((await _newest(lab_scratch)).values()))["evidence"]["queries"][0]["scope"] == "asker"


async def test_someone_allocated_to_no_building_gets_no_values_and_nothing_is_read(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    _asker(monkeypatch, {"scope": "asker", "building_ids": [], "email": "nobody@example.com"})
    await auto_expected.draft_for_live(None, now=NOW)
    assert reads == [], "Hoistra refuses such a read too"
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"asker_has_no_buildings"}


async def test_the_planner_gets_the_run_plan_not_the_plan_it_was_given(lab_scratch, main_scratch, monkeypatch):
    """Ruling 11: the steps can refocus a plan, so the values and the sentence read the plan as run."""
    await _live_run(main_scratch)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [_hoistra(q, plan={"given": True})], "none": None})
    _records(monkeypatch, run=dict(run_with_records(25), plan=PLAN_LIST_WOS))

    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        return dict(run_with_records(25), plan=PLAN_LIST_WOS)
    monkeypatch.setattr(records_run, "run_query", run_query)
    seen = []
    real_values, real_sentence = records_values.values_of, records_values.sentence_of
    monkeypatch.setattr(records_values, "values_of", lambda onto, plan, run: seen.append(("values", plan)) or real_values(onto, plan, run))
    monkeypatch.setattr(records_values, "sentence_of", lambda onto, plan, run, facts: seen.append(("sentence", plan)) or real_sentence(onto, plan, run, facts))
    await auto_expected.draft_for_live(None, now=NOW)
    assert seen and all(plan == PLAN_LIST_WOS for _what, plan in seen), "never the plan before the steps"
    assert next(iter((await _newest(lab_scratch)).values()))["evidence"]["queries"][0]["plan"] == PLAN_LIST_WOS


async def test_several_queries_are_several_blocks_in_order_and_one_sentence(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    two = [_hoistra("decisions owed", tool_id="t1"), _hoistra("inspection reports", tool_id="t2")]
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": two, "none": None})
    reads = _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    row = next(iter((await _newest(lab_scratch)).values()))
    assert [b["question"] for b in row["evidence"]["queries"]] == ["decisions owed", "inspection reports"]
    assert [r["question"] for r in reads[:2]] == ["decisions owed", "inspection reports"]
    assert row["expected"] == " ".join(b["sentence"] for b in row["evidence"]["queries"])


async def test_what_changed_since_hoistra_read_is_stored_beside_the_values(lab_scratch, main_scratch, monkeypatch):
    """Task 4 amendment + Ruling 12: the SQL Hoistra recorded under its records call, read from the trace, is compared with
    the re-run's row counts; only a query that is Hoistra's own has anything to compare."""
    from trace_lab import eval_store

    await _live_run(main_scratch)
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("turn-b1",), at=T0 + timedelta(minutes=5))
    plan = {"question_type": "list", "focus": {"concept": "WorkOrder", "match": None, "states": ["open"], "filters": []},
            "constraints": [], "include": [], "aggregate": None}
    nodes = [tool("t1", "answer_from_records", {"question": "How many work orders are open?"}, seq=1),
             llm("p1", parent="t1", plan=plan, seq=2), db_span("d1", "t1", "count", 7, seq=3),
             db_span("d2", "t1", "focus WorkOrder", 0, seq=4)]
    read_for = []

    async def nodes_of(conn, trace_id):
        read_for.append(trace_id)
        return nodes
    monkeypatch.setattr(eval_store, "nodes_of", nodes_of)
    monkeypatch.setattr(records_queries, "queries_for", _REAL_QUERIES_FOR)
    _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    assert set(read_for) == {"turn-a1", "turn-a2", "turn-b1"}
    for row in (await _newest(lab_scratch)).values():
        block = row["evidence"]["queries"][0]
        assert block["source"] == "hoistra" and block["question"] == "How many work orders are open?"
        assert block["changed"] == [{"label": "count", "then": 7, "now": 1}], \
            "Hoistra read 7 rows for its count, the re-run read 1 (its row count): the page can say so"


async def test_a_proposed_or_fixed_query_has_nothing_to_compare_with(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    prop = {"source": "proposed", "plan": PLAN_LIST_WOS, "question": "How many work orders are open?"}
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [prop], "none": None})
    _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    block = next(iter((await _newest(lab_scratch)).values()))["evidence"]["queries"][0]
    assert block["source"] == "proposed" and block["changed"] == []


async def test_a_turn_of_another_company_is_not_read_for_this_question(lab_scratch, main_scratch, monkeypatch):
    """The trace is filed under another company than the question's: nothing is read and nothing is drafted for it."""
    code = await _live_run(main_scratch)
    await _spec()
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"), org=OTHER_ORG)
    asked = _model(monkeypatch)
    calls = _queries(monkeypatch)
    _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    qs = (await runs.list_questions(member(), code))["questions"]
    own = [q for q in qs if q["turn_ids"][0] == "turn-b1"]
    assert out["drafted"] == 1 and set(await _newest(lab_scratch)) == {q["id"] for q in own}
    assert [c["question"] for c in calls] == ["Which vendors are blocked?"] and len(asked) == 0


async def test_a_turn_with_no_company_is_not_drafted_with_values(lab_scratch, main_scratch, monkeypatch):
    calls = _queries(monkeypatch)
    reads = _records(monkeypatch)
    got = await auto_expected._read_values({"id": "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f", "question": "How many floors?",
                                            "org": None}, NOW, {"proposals": 10, "onto": None})
    assert got == {"state": "none", "reason": "no_company"} and calls == [] and reads == []


async def test_no_query_keeps_the_spec_draft_and_stores_why(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    version = await _spec()
    _model(monkeypatch)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 0
    for r in await _drafts(lab_scratch):
        assert r["evidence"]["drafted_from"] == "SPEC.md" and r["evidence"]["no_query"] == "planner_unavailable"
        assert r["evidence"]["spec_version"] == version and r["evidence"]["draft"] == 3


async def test_a_turn_that_used_the_records_tool_but_left_no_readable_plan_says_so(lab_scratch, main_scratch, monkeypatch):
    """Ruling 10: not "not_about_records": the turn was about records, its plan could not be found in the trace."""
    from trace_lab import eval_store

    await _live_run(main_scratch)
    await _spec()
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("turn-b1",), at=T0 + timedelta(minutes=5))
    _model(monkeypatch)

    async def nodes_of(conn, trace_id):
        return [tool("t1", "answer_from_records", {"question": "x"}, out='{"ok": false, "error": "no plan"}')]
    monkeypatch.setattr(eval_store, "nodes_of", nodes_of)

    async def not_about(nodes, question, *, propose):
        return {"queries": [], "none": "not_about_records"}
    monkeypatch.setattr(records_queries, "queries_for", not_about)
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"records_plan_unreadable"}


async def test_a_turn_that_is_not_about_records_says_so(lab_scratch, main_scratch, monkeypatch):
    from trace_lab import eval_store

    await _live_run(main_scratch)
    await _spec()
    await seed_session(lab_scratch, CHAT_A, tag="chat", turn_ids=("turn-a1", "turn-a2"))
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("turn-b1",), at=T0 + timedelta(minutes=5))
    _model(monkeypatch)

    async def nodes_of(conn, trace_id):
        return [tool("t1", "write_todos")]
    monkeypatch.setattr(eval_store, "nodes_of", nodes_of)
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"not_about_records"}


async def test_a_blank_question_never_gets_a_proposal(lab_scratch, main_scratch, monkeypatch):
    """Ruling 13: the planner is never asked for a question with no words."""
    calls = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": None})
    got = await auto_expected._read_values({"id": "0f0f0f0f-0f0f-4f0f-8f0f-0f0f0f0f0f0f", "question": "  \n ", "org": ORG},
                                           NOW, {"proposals": 10, "onto": None})
    assert [c["propose"] for c in calls] == [False]
    assert got == {"state": "none", "reason": "no_question"}


async def test_at_most_ten_proposals_a_round(lab_scratch, main_scratch, monkeypatch):
    await _questions(main_scratch, 12)
    # a proposal needs the turn's trace, with a data tool other than the records tool (final review 9)
    await _file_traces(lab_scratch, monkeypatch, nodes=[tool("t1", "get_compliance_status")], questions=12)
    _model(monkeypatch)
    _records(monkeypatch)
    proposals = []

    def answer(nodes, question, propose):
        if not propose:
            return {"queries": [], "none": None}            # no proposal asked for: it needs one
        proposals.append(question)
        return {"queries": [{"source": "proposed", "plan": PLAN_LIST_WOS, "question": question}], "none": None}
    _queries(monkeypatch, answer)
    # The mock proposes for every question, as if none had a key in the answer key (these do): with a key, a proposal
    # would be read again from the key (auto_expected._stale_key, 23 Nov 2026), which is not what this counts.
    monkeypatch.setattr(auto_expected.answer_key, "match", lambda q: None)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert len(proposals) == 10 and out["drafted"] == 10, "the other two wait for the next round"
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert len(proposals) == 12 and out["drafted"] == 2
    assert len(await _newest(lab_scratch)) == 12


async def test_only_a_planner_call_spends_the_proposal_budget(lab_scratch, main_scratch, monkeypatch):
    """Hoistra's own query, a fixed one, and "not about records" cost no model call, so they take none of the ten."""
    await _questions(main_scratch, 14)
    await _spec()
    _model(monkeypatch)
    _records(monkeypatch)
    asked_with_proposal = []

    def answer(nodes, question, propose):
        asked_with_proposal.append(propose)
        n = int(question.split()[-1].rstrip("?"))
        if n % 2:
            return {"queries": [], "none": "not_about_records"}     # no data tool: no planner call was made
        return {"queries": [_hoistra(question)], "none": None}       # Hoistra's own: no planner call either
    _queries(monkeypatch, answer)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 14 and all(asked_with_proposal), "none of the 14 used up the budget"


async def test_older_drafts_get_values_but_people_and_reviews_are_kept(lab_scratch, main_scratch, monkeypatch):
    code = await _questions(main_scratch, 4)
    version = await _spec()
    qs = sorted((await runs.list_questions(member(), code))["questions"], key=lambda q: q["asked_at"])
    old_spec = {"drafted_from": "SPEC.md", "spec_version": version, "rules": []}
    old_key = {"drafted_from": "answer_key", "key": "vendors_blocked", "params": {}}
    async with lab_scratch.begin() as c:
        for q, ev in zip(qs, (old_spec, old_key, old_spec, old_spec)):
            await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected="Old.", expected_id=None,
                                        evidence=ev, source="coding_agent")
    await runs.set_expected(member(), qs[2]["code"], expected="A person's version.")
    old = await _newest(lab_scratch)
    await _seed_lab_review(lab_scratch, old[qs[3]["id"]]["id"])
    asked = _model(monkeypatch)
    _queries(monkeypatch)
    _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 2 and out["keyed"] == 2 and out["drafted"] == 0 and asked == []
    new = await _newest(lab_scratch)
    for q in qs[:2]:
        assert new[q["id"]]["evidence"]["drafted_from"] == "records" and new[q["id"]]["evidence"]["draft"] == 3
        assert new[q["id"]]["evidence"]["replaced_draft"] == old[q["id"]]["id"]
    assert new[qs[2]["id"]]["expected"] == "A person's version."
    assert new[qs[3]["id"]]["id"] == old[qs[3]["id"]]["id"], "a reviewed draft is left"
    assert len(await _drafts(lab_scratch)) == 4 + 1 + 2, "the old drafts stay as history"


async def test_an_older_values_draft_with_no_query_now_is_left_as_it_is_and_costs_no_model_call(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _spec()
    qs = (await runs.list_questions(member(), code))["questions"]
    async with lab_scratch.begin() as c:
        for q in qs:
            await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected="2 vendors are blocked.",
                                        expected_id=None, source="coding_agent",
                                        evidence={"drafted_from": "answer_key", "key": "vendors_blocked", "params": {}})
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 0 and asked == [], "values are not turned into prose"
    rows = await _drafts(lab_scratch)
    assert len(rows) == 6 and {r["expected"] for r in rows} == {"2 vendors are blocked."}, "only the try is written down"
    assert {r["evidence"].get("tries") for r in await _newest_rows(lab_scratch)} == {1}


async def test_an_older_values_draft_that_cannot_be_read_again_backs_off_instead_of_being_tried_every_round(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)
    qs = (await runs.list_questions(member(), code))["questions"]
    async with lab_scratch.begin() as c:
        for q in qs:
            await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected="2 vendors are blocked.",
                                        expected_id=None, source="coding_agent",
                                        evidence={"drafted_from": "answer_key", "key": "vendors_blocked", "params": {}})
    tried = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "not_about_records"})
    await auto_expected.draft_for_live(None, now=NOW)
    assert len(tried) == 3
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert len(tried) == 3, "not read again a minute later: the round runs every minute"
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert len(tried) == 3, "the second try waits longer than the first: two hours"
    await auto_expected.draft_for_live(None, now=NOW + timedelta(hours=2, minutes=1))
    assert len(tried) == 6, "tried again after the longer rest"
    # ...and the values replace the draft the first time a query can be made for the question.
    _queries(monkeypatch)
    _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW + timedelta(hours=11))
    assert out["redrafted"] == 3 and out["keyed"] == 3


async def test_a_spec_draft_whose_trace_was_not_filed_is_read_again_and_its_reason_updated_when_it_is_terminal(lab_scratch, main_scratch, monkeypatch):
    """"no_trace" is a passing reason. When the traces are filed and the turns are about nothing, that is the new reason
    (a new version of the draft, the words unchanged) and the question is not read again."""
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"no_trace"}
    await _file_traces(lab_scratch, monkeypatch)                     # the traces are filed since: not about records
    tried = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "not_about_records"})
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert len(tried) == 3
    newest = await _newest_rows(lab_scratch)
    assert {r["evidence"]["no_query"] for r in newest} == {"not_about_records"}, "the stored reason is no longer wrong"
    assert {r["evidence"]["drafted_from"] for r in newest} == {"SPEC.md"} and len(await _drafts(lab_scratch)) == 6
    for later in (timedelta(minutes=45), timedelta(hours=3), timedelta(days=3)):
        await auto_expected.draft_for_live(None, now=NOW + later)
    assert len(tried) == 3 and len(await _drafts(lab_scratch)) == 6, "a terminal reason is not read again"


async def test_an_older_spec_draft_with_no_query_is_drafted_again_from_the_spec(lab_scratch, main_scratch, monkeypatch):
    """Draft 3 stores why there are no values: an older SPEC.md draft is redrafted by the same prompt, with the reason."""
    code = await _live_run(main_scratch)
    await _seed_placeholders(lab_scratch, code, await _spec())
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 3 and len(asked) == 3
    assert {r["evidence"]["no_query"] for r in (await _newest(lab_scratch)).values()} == {"no_trace"}


async def test_a_values_draft_is_not_redrafted_when_the_spec_changes(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    first = await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    _records(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["drafted"] == 3
    second = await _spec()
    assert second != first
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 0 and out["drafted"] == 0 and asked == []
    assert {r["evidence"]["drafted_from"] for r in await _drafts(lab_scratch)} == {"records"}
    assert len(await _drafts(lab_scratch)) == 3


async def test_the_records_map_is_not_built_yet_so_the_spec_draft_is_kept_and_the_values_come_when_it_is(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch, raise_=records_run.NotReady("not built"))
    first = await auto_expected.draft_for_live(None, now=NOW)
    assert first["drafted"] == 3 and first["keyed"] == 0
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"no_map"}
    assert len(reads) == 1, "every read rests once the map is known to be missing"
    asked = _model(monkeypatch)
    _records(monkeypatch)
    soon = await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert soon["replaced"] == 0, "not read again within the rest"
    later = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert later["replaced"] == 3 and later["keyed"] == 3 and later["redrafted"] == 0 and asked == [], "no model call"
    for q in (await runs.list_questions(member(), code))["questions"]:
        assert (await runs.turn_expected(member(), q["turn_ids"][0]))["kind"] == "records"
    assert [r["evidence"]["drafted_from"] for r in await _drafts(lab_scratch)] == ["SPEC.md"] * 3 + ["records"] * 3
    assert (await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST * 3))["replaced"] == 0


async def test_a_planner_that_was_down_is_asked_again_after_the_rest_and_the_values_replace_the_draft(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)       # Hoistra's own query is known first (final review 9)
    await _spec()
    _model(monkeypatch)
    down = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    tried = len(down)
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert len(down) == tried, "not asked again a minute later"
    up = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [{"source": "proposed", "plan": PLAN_LIST_WOS, "question": q}], "none": None})
    out = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert out["replaced"] == 3 and len(up) == 3
    assert {r["evidence"]["queries"][0]["source"] for r in (await _newest(lab_scratch)).values()} == {"proposed"}


async def test_a_read_that_fails_keeps_the_spec_draft_and_says_the_read_failed(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    _records(monkeypatch, raise_for="blocked", raise_=RuntimeError("the connection was closed"))
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 2
    new = await _newest(lab_scratch)
    by_text = {q["question"]: new[q["id"]] for q in (await runs.list_questions(member(), code))["questions"]}
    assert by_text["Which vendors are blocked?"]["evidence"]["no_query"] == "read_failed"
    assert all(r["evidence"]["drafted_from"] == "records" for t, r in by_text.items() if "blocked" not in t)


async def test_a_plan_that_does_not_fit_the_database_is_a_terminal_reason_never_read_again(lab_scratch, main_scratch, monkeypatch):
    """Review I1: a ValueError from the re-run ("the plan does not fit this database") is the same on every try."""
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch, raise_=ValueError("the plan does not fit this database: no such column"))
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"plan_does_not_fit"}
    tried = len(reads)
    for later in (timedelta(minutes=45), timedelta(hours=3), timedelta(days=3)):
        await auto_expected.draft_for_live(None, now=NOW + later)
    assert len(reads) == tried and len(await _drafts(lab_scratch)) == 3


async def test_one_failed_read_of_several_stores_no_half_an_answer(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    two = [_hoistra("decisions owed", tool_id="t1"), _hoistra("inspection reports", tool_id="t2")]
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": two, "none": None})
    _records(monkeypatch, raise_for="inspection", raise_=RuntimeError("timeout"))
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["drafted_from"] for r in await _drafts(lab_scratch)} == {"SPEC.md"}


async def test_the_answer_keys_fixed_query_is_one_source_and_its_off_switch_still_works(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    asked = _model(monkeypatch)
    fixed = {"source": "answer_key", "plan": PLAN_LIST_WOS, "question": "x"}
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [fixed], "none": None})
    _records(monkeypatch)
    monkeypatch.setenv("TRACE_LAB_ANSWER_KEY", "false")
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["keyed"] == 0 and out["drafted"] == 3 and len(asked) == 3, "off: the SPEC.md draft"


async def test_a_persons_version_saved_from_a_values_draft_is_a_records_answer_too(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    _queries(monkeypatch)
    _records(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW)
    q = (await runs.list_questions(member(), code))["questions"][-1]
    turn = q["turn_ids"][0]
    drafted = await runs.turn_expected(member(), turn)
    assert drafted["kind"] == "records" and drafted["draft"] is True
    # Accept: the person saves the values as their own version, as the session page sends it.
    await runs.set_expected(member(), q["code"], expected=drafted["expected"]["expected"],
                            evidence=drafted["expected"]["evidence"])
    took = await runs.turn_expected(member(), turn)
    assert took["kind"] == "records" and took["draft"] is False and took["from_draft"] is True
    await runs.set_expected(member(), q["code"], expected="A person's own words.")
    mine = await runs.turn_expected(member(), turn)
    assert mine["kind"] == "person" and mine["from_draft"] is False


async def test_a_values_draft_waits_on_the_session_page_not_in_the_loop(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    _queries(monkeypatch)
    _records(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["keyed"] == 3
    assert (await loop.proposals(member()))["expected"] == []


# ── The asker's access, rebuilt with Hoistra's own principal code (Task 4 amendment) ────────────────────────────────

B1, B2 = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
USER, BOSS, STRANGER, GHOST = (f"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa{i}" for i in "1234")


async def _accounts(main):
    async with main.begin() as c:
        await c.execute(text("CREATE TABLE plenum_cafm.users (id uuid PRIMARY KEY, email text, organization_id uuid, platform_role text)"))
        await c.execute(text("CREATE TABLE plenum_cafm.user_buildings (user_id uuid, building_id uuid)"))
        for uid, email, org, role in ((USER, "pat@example.com", ORG, "user"), (BOSS, "boss@example.com", ORG, "admin"),
                                      (STRANGER, "sam@example.com", OTHER_ORG, "user"), ("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5", "none@example.com", ORG, "user")):
            await c.execute(text("INSERT INTO plenum_cafm.users VALUES (CAST(:i AS uuid), :e, CAST(:o AS uuid), :r)"),
                            {"i": uid, "e": email, "o": org, "r": role})
        for uid, b in ((USER, B1), (USER, B2), (STRANGER, B1)):
            await c.execute(text("INSERT INTO plenum_cafm.user_buildings VALUES (CAST(:u AS uuid), CAST(:b AS uuid))"), {"u": uid, "b": b})


COMPANY = {"scope": "company", "building_ids": None, "email": None}


async def test_a_plain_user_is_rebuilt_with_the_buildings_allocated_to_them(lab_scratch, main_scratch, monkeypatch):
    await _accounts(main_scratch)
    got = await _REAL_ASKER_SCOPE(ORG, USER)
    assert got["scope"] == "asker" and got["email"] == "pat@example.com" and sorted(got["building_ids"]) == [B1, B2]


async def test_an_administrator_is_not_bounded_by_allocation(lab_scratch, main_scratch, monkeypatch):
    await _accounts(main_scratch)
    assert await _REAL_ASKER_SCOPE(ORG, BOSS) == {"scope": "asker", "building_ids": None, "email": "boss@example.com"}


async def test_a_user_allocated_to_nothing_is_rebuilt_as_allocated_to_nothing(lab_scratch, main_scratch, monkeypatch):
    await _accounts(main_scratch)
    got = await _REAL_ASKER_SCOPE(ORG, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa5")
    assert got == {"scope": "asker", "building_ids": [], "email": "none@example.com"}


async def test_an_asker_that_cannot_be_rebuilt_is_the_whole_company(lab_scratch, main_scratch, monkeypatch):
    await _accounts(main_scratch)
    assert await _REAL_ASKER_SCOPE(ORG, None) == COMPANY, "no user id on the turn"
    assert await _REAL_ASKER_SCOPE(ORG, GHOST) == COMPANY, "no such account"
    assert await _REAL_ASKER_SCOPE(ORG, STRANGER) == COMPANY, "a person of another company did not ask here"
    assert await _REAL_ASKER_SCOPE(ORG, "not-a-uuid") == COMPANY, "an id that is not an id"


async def test_without_the_accounts_tables_the_asker_cannot_be_read_and_is_not_guessed_as_the_company(lab_scratch, main_scratch, monkeypatch):
    """Review I3: a read that fails is not "this person has no restriction": it is a failed read, to be tried again."""
    with pytest.raises(auto_expected.AskerUnreadable):
        await _REAL_ASKER_SCOPE(ORG, USER)


async def test_the_caller_is_built_by_hoistras_own_principal_code(lab_scratch, main_scratch, monkeypatch):
    """Trace Lab rebuilds no principal of its own: what the accounts say goes through svc-deepagents' _from_me."""
    from src.services import principal as hoistra

    await _accounts(main_scratch)
    real, bodies = hoistra._from_me, []
    monkeypatch.setattr(hoistra, "_from_me", lambda body: bodies.append(body) or real(body))
    got = await _REAL_ASKER_SCOPE(ORG, USER)
    assert len(bodies) == 1 and bodies[0]["user"]["id"] == USER and bodies[0]["user"]["role"] == "user"
    assert sorted(bodies[0]["user"]["building_ids"]) == [B1, B2] and got["scope"] == "asker"
    monkeypatch.setattr(hoistra, "_from_me", lambda body: (_ for _ in ()).throw(ValueError("an id that is not a uuid")))
    with pytest.raises(auto_expected.AskerUnreadable):
        await _REAL_ASKER_SCOPE(ORG, USER)


# ── Review fix round 1 (19 Nov 2026) ───────────────────────────────────────────────────────────────────────────────────
# I1  a draft that could not be read is read again with a backoff (30 minutes, 2 hours, 8 hours) and a cap of three tries,
#     written on the draft itself so a restart does not forget; a terminal reason replaces a stale one; a plan that does not
#     fit the database is terminal.
# I2  a question that ends with no draft stored (no approved spec, the SPEC.md call failing) does not ask the planner again
#     every round.
# I3  an asker that cannot be read is a failed read to try again, not a quiet whole-company read.

def _forget(monkeypatch):
    """What a restart does: the process remembers nothing; the drafts alone say when to try again."""
    monkeypatch.setattr(auto_expected, "_resting", {})
    monkeypatch.setattr(auto_expected, "_settled", {})
    monkeypatch.setattr(auto_expected, "_looks", {})
    monkeypatch.setattr(auto_expected, "_held", {}, raising=False)


async def test_a_retry_that_still_fails_backs_off_and_stops_after_three_tries(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    down = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    await auto_expected.draft_for_live(None, now=NOW)
    assert len(down) == 3 and {r["evidence"]["tries"] for r in await _drafts(lab_scratch)} == {0}

    async def at(later):
        _forget(monkeypatch)
        await auto_expected.draft_for_live(None, now=NOW + later)
        return len(down)
    assert await at(timedelta(minutes=29)) == 3, "the first retry waits 30 minutes"
    assert await at(timedelta(minutes=31)) == 6
    assert await at(timedelta(hours=2)) == 6, "the second waits two hours after the first"
    assert await at(timedelta(minutes=152)) == 9
    assert await at(timedelta(hours=9)) == 9, "the third waits eight hours after the second"
    assert await at(timedelta(hours=11)) == 12
    assert await at(timedelta(days=30)) == 12, "three tries, then it stops: no planner call every 30 minutes for ever"
    newest = await _newest_rows(lab_scratch)
    assert {r["evidence"]["tries"] for r in newest} == {3} and {r["evidence"]["no_query"] for r in newest} == {"planner_unavailable"}
    assert {r["evidence"]["last_tried"] for r in newest} == {(NOW + timedelta(hours=11)).isoformat()}
    assert len(await _drafts(lab_scratch)) == 3 + 9, "each try is a new version of the draft, the words unchanged"
    assert {r["expected"] for r in await _drafts(lab_scratch)} == {"Lists each overdue certificate with its days overdue, as a list."}


async def test_the_reason_stored_with_a_draft_follows_the_latest_verdict(lab_scratch, main_scratch, monkeypatch):
    """planner_unavailable at first; later the planner says the question is not about records: that is what is stored."""
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    await auto_expected.draft_for_live(None, now=NOW)
    await _file_traces(lab_scratch, monkeypatch)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "not_about_records"})
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=31))
    assert {r["evidence"]["no_query"] for r in await _newest_rows(lab_scratch)} == {"not_about_records"}


async def test_a_read_that_fails_for_a_passing_reason_backs_off_the_same_way(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch, raise_=RuntimeError("the connection was closed"))
    await auto_expected.draft_for_live(None, now=NOW)
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"read_failed"}
    n = len(reads)
    _forget(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=31))
    m = len(reads)
    assert m > n
    _forget(monkeypatch)
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=62))
    assert len(reads) == m, "the second retry is two hours after the first"


async def test_without_an_approved_spec_a_question_that_needs_the_planner_asks_it_once(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await _file_traces(lab_scratch, monkeypatch)
    calls = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "not_about_records"})
    for later in (0, 0, 1):
        await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=later))
    assert len(calls) == 3, "once per question, not every round (up to ten model calls a minute with no spec)"
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=31))
    assert len(calls) == 6, "asked again after the rest"
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=32))
    assert len(calls) == 6
    # The spec is approved: the question is drafted with the reason already known, with no new planner call.
    await _spec()
    asked = _model(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=33))
    assert out["drafted"] == 3 and len(asked) == 3 and len(calls) == 6
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"not_about_records"}


async def test_a_spec_call_that_keeps_failing_does_not_ask_the_planner_each_round(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch, fail=True)
    calls = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    first = await auto_expected.draft_for_live(None, now=NOW)
    second = await auto_expected.draft_for_live(None, now=NOW)
    assert first["failed"] == 3 and second["failed"] == 3, "the SPEC.md call is still tried every round"
    assert len(calls) == 3, "but the planner is not"
    _model(monkeypatch)
    assert (await auto_expected.draft_for_live(None, now=NOW))["drafted"] == 3 and len(calls) == 3
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"planner_unavailable"}


async def test_an_asker_that_cannot_be_read_is_a_failed_read_not_the_whole_company(lab_scratch, main_scratch, monkeypatch):
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)

    async def unreadable(org, user_id):
        raise auto_expected.AskerUnreadable("permission denied for table user_buildings")
    monkeypatch.setattr(auto_expected, "asker_scope", unreadable)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert reads == [] and out["keyed"] == 0 and out["drafted"] == 3, "nothing is read with a guessed access"
    assert {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"read_failed"}
    # Once the accounts can be read again, the values replace the draft (the retry rules of the other reasons apply).
    _asker(monkeypatch)
    _forget(monkeypatch)
    later = await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=31))
    assert later["replaced"] == 3 and len(reads) == 3
    assert {r["evidence"]["queries"][0]["scope"] for r in await _newest_rows(lab_scratch)} == {"asker"}


async def test_a_turn_record_that_cannot_be_read_is_a_failed_read_not_the_whole_company(lab_scratch, main_scratch, monkeypatch):
    from contextlib import asynccontextmanager

    from trace_lab import conversations

    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)

    @asynccontextmanager
    async def broken():
        raise RuntimeError("could not connect to the application database")
        yield
    monkeypatch.setattr(conversations, "_read", broken)
    await auto_expected.draft_for_live(None, now=NOW)
    assert reads == [] and {r["evidence"]["no_query"] for r in await _drafts(lab_scratch)} == {"read_failed"}


async def test_an_asker_that_cannot_be_read_is_logged_as_a_warning(lab_scratch, main_scratch, monkeypatch):
    seen = []

    class Log:
        def __getattr__(self, level):
            return lambda event, **kw: seen.append((level, event))
    monkeypatch.setattr(auto_expected, "log", Log())
    with pytest.raises(auto_expected.AskerUnreadable):
        await _REAL_ASKER_SCOPE(ORG, USER)              # no accounts tables in this database
    assert [lvl for lvl, _ in seen] == ["warning"]
    assert await _REAL_ASKER_SCOPE(ORG, None) == COMPANY, "no user id on the turn: deterministic, quietly the company"
    assert [lvl for lvl, _ in seen] == ["warning"]


async def test_a_verdict_the_process_still_remembers_is_not_counted_as_a_try(lab_scratch, main_scratch, monkeypatch):
    """A draft that is due but whose question's verdict is still remembered is not read: no try is written on it."""
    await _live_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    down = _queries(monkeypatch, lambda nodes, q, propose: {"queries": [], "none": "planner_unavailable"})
    await auto_expected.draft_for_live(None, now=NOW)
    for qid in list(auto_expected._settled):
        auto_expected._settled[qid] = (NOW + timedelta(hours=5), "planner_unavailable")
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=31))
    assert len(down) == 3 and len(await _drafts(lab_scratch)) == 3
    assert {r["evidence"]["tries"] for r in await _newest_rows(lab_scratch)} == {0}


# ── Final review (19 Nov 2026) ──
# 1   (live) a plan carrying the records engine's own objects stopped every round; one question's failure stops no round.
# 8   the chat's own record of a turn guards the company, as the trace does.
# 9   a turn whose trace is not filed yet is not drafted from the answer key: Hoistra's own query comes first.
# 10  a question that can be decided for no company rests instead of taking a slot every round.

def _log(monkeypatch):
    seen = []

    class Log:
        def __getattr__(self, level):
            return lambda event, **kw: seen.append((level, event, kw))
    monkeypatch.setattr(auto_expected, "log", Log())
    return seen


async def _qid(code, text_):
    return next(q["id"] for q in (await runs.list_questions(member(), code))["questions"] if q["question"] == text_)


async def test_a_plan_carrying_the_records_engines_own_objects_is_still_stored(lab_scratch, main_scratch, monkeypatch):
    """Live, 19 Nov 2026: a certificate question naming a building carries Hoistra's Relation objects in its plan as run
    (an include's "_scope"); 'Object of type Relation is not JSON serializable' stopped every round after the first."""
    from src.agents import ontology_qa as oq

    rel = oq.Relation("cert_in_building", {"from": "ComplianceCertificate", "to": "Building", "join": ["from.building_id = to.id"]})
    plan = dict(PLAN_LIST_WOS, include=[{"name": "certificates", "concept": "ComplianceCertificate", "via": ["CERTIFICATE_BUILDING"],
                                         "states": [], "filters": [], "_scope": [({"concept": "Building"}, [(rel, True)])]}])
    await _live_run(main_scratch)
    _queries(monkeypatch, lambda nodes, q, propose: {"queries": [_hoistra(q, plan=plan)], "none": None})
    _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 3 and out["keyed"] == 3
    assert all(r["evidence"]["drafted_from"] == "records" for r in await _drafts(lab_scratch))


async def test_one_question_that_fails_unexpectedly_does_not_stop_the_round_and_rests(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    calls = _queries(monkeypatch)
    _records(monkeypatch)
    seen = _log(monkeypatch)
    bad = await _qid(code, "Which vendors are blocked?")
    real = runs._insert_expected

    async def insert(conn, m, *, question_id, **kw):
        if question_id == bad:
            raise TypeError("Object of type Relation is not JSON serializable")
        return await real(conn, m, question_id=question_id, **kw)
    monkeypatch.setattr(runs, "_insert_expected", insert)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["drafted"] == 2 and out["keyed"] == 2, "the other questions are still drafted"
    assert bad not in await _newest(lab_scratch)
    assert any(lvl == "warning" and kw.get("question_id") == bad for lvl, _e, kw in seen), "said as a warning"
    tried = len(calls)
    await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1))
    assert len(calls) == tried, "it rests: not tried again a minute later"
    monkeypatch.setattr(runs, "_insert_expected", real)
    again = await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    assert again["drafted"] == 1 and bad in await _newest(lab_scratch), "tried again after the rest"


async def test_a_chat_turn_of_another_company_is_not_read_for_this_question(lab_scratch, main_scratch, monkeypatch):
    """The trace is not filed yet, so the chat's own record of the turn is the only guard on its company."""
    code = await _live_run(main_scratch)
    async with main_scratch.begin() as c:
        await c.execute(text("UPDATE plenum_cafm.agent_trace_turns SET organization_id = CAST(:o AS uuid) WHERE turn_id = 'turn-b1'"),
                        {"o": OTHER_ORG})
    calls = _queries(monkeypatch)
    reads = _records(monkeypatch)
    who = _asker(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    other = await _qid(code, "Which vendors are blocked?")
    assert out["drafted"] == 2 and other not in await _newest(lab_scratch)
    assert "Which vendors are blocked?" not in [c["question"] for c in calls] + [r["question"] for r in reads]
    assert len(who) == 2, "nobody of another company is rebuilt as the asker"


async def test_a_turn_whose_trace_is_not_filed_is_not_drafted_from_the_key_and_hoistras_query_comes_first(lab_scratch, main_scratch, monkeypatch):
    """Source order Hoistra -> fixed -> proposed: before the trace is filed nothing is known of Hoistra's own query, so the
    answer key does not draft (a key draft is never put over by Hoistra's query later)."""
    code = await _mixed_run(main_scratch)
    await _spec()
    _model(monkeypatch)
    seen = _key_reads(monkeypatch)
    first = await auto_expected.draft_for_live(None, now=NOW)
    keyed = await _qid(code, "Which vendors are blocked?")
    row = (await _newest(lab_scratch))[keyed]
    assert first["keyed"] == 0 and seen == [], "nothing read from the key before the trace is filed"
    assert row["evidence"]["drafted_from"] == "SPEC.md" and row["evidence"]["no_query"] == "no_trace"
    plan = {"question_type": "list", "focus": {"concept": "Vendor", "match": None, "states": ["blocked"], "filters": []},
            "constraints": [], "include": [], "aggregate": None}
    await _file_traces(lab_scratch, monkeypatch, nodes=[
        tool("t1", "answer_from_records", {"question": "Which vendors are blocked?"}, seq=1), llm("p1", parent="t1", plan=plan, seq=2)])
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=1))
    block = (await _newest(lab_scratch))[keyed]["evidence"]["queries"][0]
    assert block["source"] == "hoistra", "Hoistra's own query, read from its trace"


async def test_a_question_of_another_companys_turn_rests_and_takes_no_slot_of_the_round(lab_scratch, main_scratch, monkeypatch):
    code = await _live_run(main_scratch)
    await seed_session(lab_scratch, CHAT_B, tag="chat", turn_ids=("turn-b1",), org=OTHER_ORG, at=T0 + timedelta(minutes=5))
    _queries(monkeypatch)
    _records(monkeypatch)
    looked = []
    real = auto_expected._turn_context

    async def context(q):
        looked.append(q["id"])
        return await real(q)
    monkeypatch.setattr(auto_expected, "_turn_context", context)
    stuck = await _qid(code, "Which vendors are blocked?")
    first = await auto_expected.draft_for_live(None, now=NOW, limit=1)
    assert first["drafted"] == 0 and looked == [stuck], "the newest question is another company's turn"
    second = await auto_expected.draft_for_live(None, now=NOW + timedelta(minutes=1), limit=1)
    assert second["drafted"] == 1 and looked.count(stuck) == 1, "it rests and the slot goes to the next question"
    await auto_expected.draft_for_live(None, now=NOW + auto_expected.REST + timedelta(minutes=2), limit=1)
    assert looked.count(stuck) == 2, "looked at again after the rest"


# 2   only curators and the platform team may change what a saved answer runs (spec call 7); anyone may accept or check.

async def test_anyone_may_accept_or_check_the_values_but_only_curators_change_what_runs(lab_scratch, main_scratch, monkeypatch):
    import copy

    from trace_lab.review_errors import Forbidden

    _code, turn, _reads = await _drafted(lab_scratch, main_scratch, monkeypatch)
    viewer = member(org=ORG, role="reviewer", all_companies=False, manages=False)
    got = await runs.turn_expected(viewer, turn)
    code, words, ev = got["question"], got["expected"]["expected"], got["expected"]["evidence"]
    await runs.set_expected(viewer, code, expected=words, evidence=dict(ev, drafted_from="records"))      # Accept
    checked = copy.deepcopy(ev)
    checked["queries"][0]["facts"][0].update(check="wrong", corrected="24", note="One floor was sold.")
    await runs.set_expected(viewer, code, expected=words, evidence=checked)                               # Save my checks

    def changed(how):
        out = copy.deepcopy(ev)
        how(out["queries"][0])
        return out
    for other in (changed(lambda b: b["plan"]["focus"].update(concept="Building")),          # another query
                  changed(lambda b: b.update(source="person")),                               # read as written instead
                  changed(lambda b: b.update(question="How many buildings are there?")),      # Hoistra's steps asked otherwise
                  dict(ev, queries=ev["queries"] + [copy.deepcopy(ev["queries"][0]) | {"question": "Another?"}]),
                  {"drafted_from": "answer_key", "key": "wo_at_building", "params": {"states": ["open"], "building": "Elsewhere"}}):
        with pytest.raises(Forbidden) as e:
            await runs.set_expected(viewer, code, expected=words, evidence=other)
        assert e.value.reason == "curators_only"
    await runs.set_expected(viewer, code, expected="My own words.")                               # Edit: words only
    mine = changed(lambda b: (b["plan"]["focus"].update(concept="Building"), b.update(source="person")))
    await runs.set_expected(member(), code, expected="Building records found: 4.", evidence=mine)  # a curator may
    now = await runs.turn_expected(viewer, turn)
    assert now["expected"]["evidence"]["queries"][0]["plan"]["focus"]["concept"] == "Building"
    again = copy.deepcopy(now["expected"]["evidence"])
    again["queries"][0]["facts"][0]["check"] = "right"
    await runs.set_expected(viewer, code, expected=now["expected"]["expected"], evidence=again)     # checks on it: fine


async def test_a_values_draft_with_a_stated_figure_worded_before_is_read_again_and_no_other_draft_is(
        lab_scratch, main_scratch, monkeypatch):
    """19 Nov 2026: "ABC states floors: 25. Floor records found: 0." now reads "ABC states floors: 25. Floor-by-floor
    entries: 0 (none entered)." Values drafts stored before (no "wording") that hold a stated figure are read again; a
    values draft without one, and a current one, are left alone, and no model is asked."""
    code = await _live_run(main_scratch)
    qs = sorted((await runs.list_questions(member(), code))["questions"], key=lambda q: q["asked_at"])
    stated = {"kind": "stated", "label": "ABC states floors", "value": 25, "receipt": "q1"}
    count = {"kind": "count", "label": "Floor records found", "value": 0, "receipt": "q2"}
    old = {"drafted_from": "records", "draft": auto_expected.DRAFT_VERSION}
    async with lab_scratch.begin() as c:
        for q, facts, extra in ((qs[0], [count, stated], {}), (qs[1], [count], {}),
                                (qs[2], [stated, count], {"wording": auto_expected.WORDING})):
            await runs._insert_expected(c, auto_expected.AUTHOR, question_id=q["id"], expected="old words",
                                        expected_id=None, source="coding_agent",
                                        evidence=dict(old, queries=[{"facts": facts}], **extra))
    before = await _newest(lab_scratch)
    asked = _model(monkeypatch)
    _queries(monkeypatch)
    reads = _records(monkeypatch)
    out = await auto_expected.draft_for_live(None, now=NOW)
    assert out["redrafted"] == 1 and out["drafted"] == 0 and asked == []
    after = await _newest(lab_scratch)
    assert after[qs[0]["id"]]["evidence"]["wording"] == auto_expected.WORDING
    assert after[qs[0]["id"]]["evidence"]["replaced_draft"] == before[qs[0]["id"]]["id"]
    assert after[qs[1]["id"]]["id"] == before[qs[1]["id"]]["id"], "no stated figure: the words did not change"
    assert after[qs[2]["id"]]["id"] == before[qs[2]["id"]]["id"], "already worded the new way"
    assert [r["question"] for r in reads] == [qs[0]["question"]]
    assert (await auto_expected.draft_for_live(None, now=NOW))["redrafted"] == 0, "read once"
