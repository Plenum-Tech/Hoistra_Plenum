"""The answer key (16 Nov 2026, Hussain and Bala): for the kinds of live question asked most - work orders, certificates,
vendors - the expected answer's values are read from the records with a FIXED plan compiled by Hoistra's own records
engine (svc-deepagents ontology_qa: the model never writes SQL there either), so they use exactly Hoistra's
definitions of open, overdue, lapsed and blocked. Every value keeps its receipt: the query, its parameters and the rows
it read, so anyone can check it, in Trace Lab or in psql. Read-only; nothing in the orchestrator changes."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from trace_lab import answer_key as ak


@pytest.mark.parametrize("question,key,params", [
    ("which building has the maximum work orders open and delayed amongst all buildings and what are the common "
     "reasons for it", "wo_most_by_building", {"states": ["open", "overdue"]}),
    ("Which building has the most open work orders?", "wo_most_by_building", {"states": ["open"]}),
    ("how many overdue work orders are there at Bishopsgate Tower?", "wo_at_building",
     {"states": ["open", "overdue"], "building": "Bishopsgate Tower"}),
    ("show the open work orders at Harbour Point", "wo_at_building", {"states": ["open"], "building": "Harbour Point"}),
    ("which certificates have lapsed at bishopsgate tower?", "certificates", {"states": ["lapsed"], "building": "bishopsgate tower"}),
    ("Which certificates are expiring soon?", "certificates", {"states": ["expiring_soon"], "building": None}),
    ("Which certificates are due for renewal?", "certificates", {"states": ["due_for_renewal"], "building": None}),
    ("How many vendors are blocked right now?", "vendors_blocked", {}),
    ("which vendors are not eligible to work here?", "vendors_blocked", {}),
])
def test_the_kinds_of_question_it_knows(question, key, params):
    found = ak.match(question)
    assert found is not None and found[0].id == key
    for k, v in params.items():
        assert found[1].get(k) == v, (k, found[1])


@pytest.mark.parametrize("question", ["tell me more about bishopsgate tower", "validate and ingest this document.",
                                      "which building has the lowest hoist score and how can i improve it", ""])
def test_anything_else_has_no_key(question):
    assert ak.match(question) is None


def test_the_plans_are_fixed_and_name_hoistras_own_states():
    key, params = ak.match("which building has the most open and delayed work orders?")
    assert key.plan(params) == {"question_type": "count",
                                "focus": {"concept": "WorkOrder", "match": None, "states": ["open", "overdue"], "filters": []},
                                "constraints": [{"concept": "Building", "match": None, "states": [], "filters": [], "via": []}],
                                "include": [], "aggregate": {"count_by": [{"concept": "Building", "attribute": "name"}]}}
    key, params = ak.match("which certificates have lapsed at Bishopsgate Tower?")
    plan = key.plan(params)
    assert plan["focus"] == {"concept": "ComplianceCertificate", "match": None, "states": ["lapsed"], "filters": []}
    assert plan["constraints"][0]["concept"] == "Building" and plan["constraints"][0]["match"] == "Bishopsgate Tower"


def _receipts():
    return [{"id": "q1", "label": "lookup Building", "sql": "SELECT 1", "params": {}, "row_count": 1, "rows": []},
            {"id": "q2", "label": "aggregate", "sql": "SELECT name, count(*) ...", "params": {"p0": "Completed"},
             "row_count": 3, "rows": []},
            {"id": "q3", "label": "count", "sql": "SELECT count(*) ...", "params": {}, "row_count": 1, "rows": []},
            {"id": "q4", "label": "focus WorkOrder", "sql": "SELECT ...", "params": {}, "row_count": 2, "rows": []}]


def test_a_count_by_building_gives_each_buildings_value_with_its_receipt():
    key, params = ak.match("which building has the most open and delayed work orders?")
    result = {"kind": "aggregate", "rows": [{"Building_name": "Bishopsgate Tower", "count": 13},
                                            {"Building_name": "Harbour Point", "count": 4},
                                            {"Building_name": "Ashgrove Court", "count": 1}]}
    facts = key.facts(params, result, _receipts())
    assert facts[0] == {"id": "f1", "label": "Bishopsgate Tower", "value": 13, "receipt": "q2"}
    assert [f["value"] for f in facts] == [13, 4, 1]
    assert ak.sentence(key, params, facts).startswith("Bishopsgate Tower has the most open and overdue work orders: 13.")


def test_a_list_gives_the_total_and_each_item_with_their_receipts():
    key, params = ak.match("which vendors are blocked?")
    result = {"kind": "records", "focus": "Vendor", "total": 2,
              "records": [{"attributes": {"vendor_name": "Pennard Fire Services", "block_state": "Blocked"}},
                          {"attributes": {"vendor_name": "Ostley Power Services", "block_state": "Blocked"}}]}
    facts = key.facts(params, result, _receipts())
    assert facts[0] == {"id": "f1", "label": "Blocked vendors", "value": 2, "receipt": "q3"}
    assert [(f["value"], f["receipt"]) for f in facts[1:]] == [("Pennard Fire Services", "q4"), ("Ostley Power Services", "q4")]
    assert ak.sentence(key, params, facts) == "2 vendors are blocked: Pennard Fire Services, Ostley Power Services."


def test_nothing_found_says_none_with_the_count_receipt():
    key, params = ak.match("which certificates have lapsed?")
    facts = key.facts(params, {"kind": "records", "focus": "ComplianceCertificate", "total": 0, "records": []}, _receipts())
    assert facts == [{"id": "f1", "label": "Lapsed certificates", "value": 0, "receipt": "q3"}]
    assert ak.sentence(key, params, facts) == "No certificates are lapsed."


def test_copy_for_psql_fills_in_every_value_and_the_company_scope():
    receipt = {"sql": "SELECT count(*) FROM work_orders f WHERE f.status NOT IN (:p0, :p1) AND f.sla_due_at < :p2"
                      " AND f.id = ANY(:p3) AND f.note = :p10",
               "params": {"p0": "Completed", "p1": "It's done", "p2": "2026-11-16T09:00:00+00:00", "p3": ["a", "b"],
                          "p10": None}}
    out = ak.psql_text(receipt, org="11111111-1111-1111-1111-111111111111")
    assert "SELECT set_config('app.udr_org', '11111111-1111-1111-1111-111111111111', false);" in out
    assert "SET search_path TO plenum_scoped, public;" in out
    assert "NOT IN ('Completed', 'It''s done')" in out and "< '2026-11-16T09:00:00+00:00'" in out
    assert "ANY(ARRAY['a', 'b'])" in out and "f.note = NULL" in out and ":p" not in out


def test_values_are_made_safe_to_store():
    out = ak.jsonable({"d": date(2026, 11, 16), "t": datetime(2026, 11, 16, 9, tzinfo=timezone.utc), "n": 3, "x": {1, 2}})
    assert out == {"d": "2026-11-16", "t": "2026-11-16T09:00:00+00:00", "n": 3, "x": [1, 2]}


# ── The orchestrator's own plans, read from its trace (16 Nov 2026). Correcting one is records_form's and the routes'
# job now (19 Nov 2026): see the end of this file. ─────────────────────────────────────────────────────────────

import json

PLAN = {"question_type": "list",
        "focus": {"concept": "ComplianceCertificate", "match": None, "states": ["expiring_soon"],
                  "filters": [{"attribute": "expiry_date", "op": "within", "value": "next_30_days"}]},
        "constraints": [{"concept": "Building", "match": "Bishopsgate", "via": [], "states": [], "filters": []}],
        "include": [], "aggregate": None}


def test_the_orchestrators_plans_are_read_from_its_trace():
    nodes = [{"type": "llm", "output": {"content": "", "tool_calls": [{"name": "answer_from_records"}]}},
             {"type": "llm", "output": {"content": "not json"}},
             {"type": "llm", "output": {"content": json.dumps(PLAN)}},
             {"type": "tool", "output": {"plan": "no"}},
             {"type": "llm", "output": {"content": "```json\n" + json.dumps(PLAN) + "\n```"}}]
    plans = ak.extract_plans(nodes)
    assert len(plans) == 1 and plans[0]["focus"]["concept"] == "ComplianceCertificate", "the same plan once"
    assert ak.extract_plans([]) == []


# ── Certificates expiring within a window (16 Nov 2026: R59's "certificates expiring in the next 30 days" had no key) ──

@pytest.mark.parametrize("question,window,building", [
    ("Which certificates expire in the next 30 days?", "next_30_days", None),
    ("which certificates are expiring within 60 days at Bishopsgate Tower?", "next_60_days", "Bishopsgate Tower"),
    ("What certificates expire in the next 2 weeks?", "next_14_days", None),
    ("which certificates expire in the next 3 months", "next_90_days", None),
    ("Which certificates expire this month?", "this_month", None),
    ("Which certificates are due to expire next month?", "next_month", None),
    # R59 (16 Nov): the same question in other words.
    ("What certificates lapse in the next 30 days?", "next_30_days", None),
    ("Which certificates are lapsing within 45 days?", "next_45_days", None),
    ("Which certificates run out in the next 2 months?", "next_60_days", None),
    ("Which certificates are due for renewal in the next 30 days?", "next_30_days", None),
    ("certificates ending in the next 7 days", "next_7_days", None),
])
def test_certificates_expiring_within_a_window(question, window, building):
    key, params = ak.match(question)
    assert key.id == "certificates_window" and params == {"window": window, "building": building}
    plan = key.plan(params)
    assert plan["focus"]["filters"] == [{"attribute": "expiry_date", "op": "within", "value": window}]
    assert plan["focus"]["states"] == []


def test_lapsed_without_a_window_stays_lapsed():
    key, params = ak.match("Which certificates have lapsed?")
    assert key.id == "certificates" and params["states"] == ["lapsed"]


def test_expiring_soon_without_a_window_keeps_hoistras_state():
    key, params = ak.match("Which certificates are expiring soon?")
    assert key.id == "certificates" and params["states"] == ["expiring_soon"]


def test_a_window_answer_reads_plainly():
    key, params = ak.match("Which certificates expire in the next 30 days?")
    facts = key.facts(params, {"kind": "records", "focus": "ComplianceCertificate", "total": 1,
                               "records": [{"attributes": {"cert_type": "LOLER", "certificate_ref": "LOLER-1", "expiry_date": "2026-12-01"}}]},
                      [{"id": "q1", "label": "count"}, {"id": "q2", "label": "focus ComplianceCertificate"}])
    assert facts[0]["label"] == "Certificates expiring within the next 30 days"
    assert ak.sentence(key, params, facts) == "1 certificate expires within the next 30 days: LOLER LOLER-1 (expiry 2026-12-01)."
    assert ak.sentence(key, params, facts[:1] and [dict(facts[0], value=0)]) == "No certificates expire within the next 30 days."


# ── Review fixes before commit (16 Nov 2026) ──

@pytest.mark.parametrize("question", ["How many open work orders are in progress at the moment?",
                                      "open work orders for HVAC"])
def test_words_that_are_not_buildings_are_not_read_as_one(question):
    assert ak._place(question) is None


# ── The answer key and "Run again now" on the faithful re-run (19 Nov 2026, Hussain and Bala). The answer key's fixed
#    plans, a stored query and a person's own plan are all read by records_run.run_query: the steps Hoistra's records
#    tool runs, read-only, for the question's own company and, for a query read as its asker, with that person's access.
#    The records and the map are faked here; the routes with the lab database are in test_trace_lab_auto_expected_sql. ──

from eval_fixtures import OTHER_ORG, ORG, member
from test_trace_lab_records_form import R62, _node, _onto, _plan
from trace_lab import auto_expected, eval_store, records_queries, records_run, runs
from trace_lab.review_errors import Forbidden, Invalid, NotFound


def _receipt(n, label, rows=None):
    return {"id": "q" + str(n), "label": label, "sql": "SELECT 1", "params": {}, "row_count": len(rows or []),
            "rows": rows or [], "psql": "BEGIN READ ONLY; SELECT 1; ROLLBACK;"}


def _run_of(plan, *, total=0, found=True, unresolved=(), notes=()):
    focus = plan["focus"]["concept"]
    receipts = [_receipt(1, "count", [{"n": total}]), _receipt(2, "focus " + focus)]
    return {"found": found, "unresolved": list(unresolved), "plan": plan, "notes": list(notes),
            "receipts": receipts if found else [], "read_at": "2026-11-19T09:00:00+00:00",
            "result": {"kind": "records", "focus": focus, "total": total, "records": [], "paths": []} if found else None,
            "pending": None}


@pytest.fixture
def reads(monkeypatch):
    """Fake the faithful re-run and the map. `reads.calls` are the reads; `reads.total` what a plan finds, by its focus."""
    class Reads:
        calls: list = []
        totals: dict = {}
        fail: Exception | None = None
        fail_mode: str | None = None
    r = Reads()
    r.calls, r.totals = [], {}

    async def load_map():
        return _onto()

    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        r.calls.append({"plan": plan, "question": question, "org": org, "mode": mode, "scope": scope})
        if r.fail is not None and r.fail_mode in (None, mode):
            raise r.fail
        return _run_of(plan, total=r.totals.get(plan["focus"]["concept"], 7))
    monkeypatch.setattr(records_run, "load_map", load_map)
    monkeypatch.setattr(records_run, "run_query", run_query)
    return r


def _block(plan=R62, question="How many floors does ABC have?", **more):
    return dict({"source": "hoistra", "question": question, "plan": plan, "found": True, "unresolved": [], "notes": [],
                 "facts": [], "receipts": [], "read_at": "2026-11-18T09:00:00+00:00", "sentence": "x", "scope": "company",
                 "scope_email": None, "changed": []}, **more)


def _turn(monkeypatch, blocks=None, *, kind="records", live=True, company=ORG, text="How many floors does ABC have?", more=None):
    evidence = {"drafted_from": "records", "draft": 3, "queries": blocks} if blocks is not None else {}
    got = dict({"kind": kind, "live": live, "company": company, "text": text, "expected": {"evidence": evidence}}, **(more or {}))

    async def turn_expected(m, turn_id):
        return got
    monkeypatch.setattr(runs, "turn_expected", turn_expected)
    return got


def _no_user(monkeypatch, user="u-1"):
    seen = []

    async def turn_user(turn_id, org):
        seen.append((turn_id, org))
        return user
    monkeypatch.setattr(ak, "_turn_user", turn_user)
    return seen


# ── the key's own run ──

async def test_the_keys_run_goes_through_the_faithful_rerun_with_the_question_asked(reads, monkeypatch):
    key, params = ak.match("which vendors are blocked?")
    out = await ak.run(key, params, ORG, question="which vendors are blocked?")
    assert reads.calls == [{"plan": key.plan(params), "question": "which vendors are blocked?", "org": ORG,
                            "mode": "hoistra", "scope": None}]
    assert out["found"] is True and out["plan"]["focus"]["concept"] == "Vendor" and out["read_at"]
    assert out["facts"] == [{"id": "f1", "label": "Blocked vendors", "value": 7, "receipt": "q1"}]
    assert out["notes"] == [] and out["receipts"][0]["psql"]


async def test_a_run_gives_what_the_steps_said(reads, monkeypatch):
    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        return _run_of(plan, notes=["Counted work orders by building"])
    monkeypatch.setattr(records_run, "run_query", run_query)
    key, params = ak.match("which vendors are blocked?")
    assert (await ak.run(key, params, ORG))["notes"] == ["Counted work orders by building"]


async def test_the_keys_values_point_at_the_answers_own_queries_not_the_ones_read_after_it(reads, monkeypatch):
    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        run = _run_of(plan, total=2)
        run["receipts"] += [_receipt(3, "count open work orders by trade"), _receipt(4, "focus pending")]
        return run
    monkeypatch.setattr(records_run, "run_query", run_query)
    key, params = ak.match("which vendors are blocked?")
    out = await ak.run(key, params, ORG)
    assert out["facts"][0]["receipt"] == "q1" and len(out["receipts"]) == 4, "all kept, the values read the answer's own"


async def test_a_name_the_records_do_not_hold_is_not_found_with_the_names(reads, monkeypatch):
    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        return _run_of(plan, found=False, unresolved=["Nowhere House"])
    monkeypatch.setattr(records_run, "run_query", run_query)
    key, params = ak.match("how many open work orders at Nowhere House?")
    out = await ak.run(key, params, ORG)
    assert out["found"] is False and out["unresolved"] == ["Nowhere House"] and out["facts"] == []


async def test_a_plan_the_records_cannot_run_and_a_map_not_built_are_raised_as_they_always_were(reads, monkeypatch):
    key, params = ak.match("which vendors are blocked?")
    reads.fail = ValueError("the plan does not fit this database: Vendor has no state 'blocked'")
    with pytest.raises(ValueError):
        await ak.run(key, params, ORG)
    reads.fail = records_run.NotReady("no map")
    with pytest.raises(ak.NotReady):
        await ak.run(key, params, ORG)


# ── Run again now ──

async def test_run_again_reads_the_stored_query_afresh_for_the_turns_company(reads, monkeypatch):
    other = _plan(_node("Building"), question_type="count")
    _turn(monkeypatch, [_block(R62, "How many floors does ABC have?"), _block(other, "How many buildings are there?")])
    out = await ak.recheck(member(), "turn-1", index=1)
    assert reads.calls == [{"plan": other, "question": "How many buildings are there?", "org": ORG, "mode": "hoistra", "scope": None}]
    assert set(out) >= {"found", "facts", "receipts", "read_at", "unresolved", "notes", "sentence"}
    assert out["found"] is True and out["facts"][0] == {"id": "f1", "kind": "count", "label": "Building records found", "value": 7, "receipt": "q1"}
    assert out["sentence"] == "Building records found: 7." and out["unresolved"] == [] and out["notes"] == []
    assert out["scope"] == "company" and out["scope_email"] is None
    assert out["receipts"][0]["psql"], "each receipt keeps its psql text"


async def test_run_again_on_a_name_the_records_do_not_hold_says_so_not_zero(reads, monkeypatch):
    async def run_query(plan, *, question, org, mode="hoistra", scope=None):
        return _run_of(plan, found=False, unresolved=["Nowhere House"])
    monkeypatch.setattr(records_run, "run_query", run_query)
    _turn(monkeypatch, [_block()])
    out = await ak.recheck(member(), "turn-1", index=0)
    assert out["found"] is False and out["unresolved"] == ["Nowhere House"]
    assert [(f["kind"], f["label"], f["value"]) for f in out["facts"]] == [("missing", "Not on record", "Nowhere House")]
    assert out["sentence"] == "Not on record: Nowhere House."


async def test_run_again_names_the_first_query_unless_told_another(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62), _block(_plan(_node("Building")))])
    await ak.recheck(member(), "turn-1")
    assert reads.calls[0]["plan"] == R62


async def test_run_again_asks_the_planners_question_not_the_turns(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62, "")], text="What does ABC hold?")
    await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls[0]["question"] == "What does ABC hold?", "a query with no question of its own is read as the turn asked"


@pytest.mark.parametrize("index", [1, 5, -1, "x", None, 1.5])
async def test_a_query_that_is_not_in_the_answer_is_refused(reads, monkeypatch, index):
    _turn(monkeypatch, [_block()])
    with pytest.raises(Invalid) as e:
        await ak.recheck(member(), "turn-1", index=index)
    assert e.value.reason == "no_query" and reads.calls == []


@pytest.mark.parametrize("kind,evidence_queries", [("person", None), ("spec", None), (None, None), ("records", [])])
async def test_an_answer_not_read_from_the_records_is_refused(reads, monkeypatch, kind, evidence_queries):
    _turn(monkeypatch, evidence_queries, kind=kind)
    with pytest.raises(Invalid):
        await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls == []


async def test_a_turn_with_no_company_is_not_read(reads, monkeypatch):
    _turn(monkeypatch, [_block()], company=None)
    with pytest.raises(Invalid):
        await ak.recheck(member(), "turn-1", index=0)


async def test_another_companys_turn_is_not_found_and_nothing_is_read(reads, monkeypatch):
    async def not_found(m, turn_id):
        raise NotFound("turn_not_found", "That turn is not in Trace Lab.")
    monkeypatch.setattr(runs, "turn_expected", not_found)
    with pytest.raises(NotFound):
        await ak.recheck(member(org=OTHER_ORG, all_companies=False, role="admin"), "turn-1", index=0)
    assert reads.calls == []


@pytest.mark.parametrize("marks", [{"edited": True}, {"source": "person", "edited_from": "hoistra"}])
async def test_a_query_a_person_changed_is_read_as_it_was_written(reads, monkeypatch, marks):
    """The steps would put back what the person took out (the deleted-vendor filter, the codes it adds). The page marks
    such a query `source: "person"`."""
    _turn(monkeypatch, [_block(R62, **marks)])
    await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls[0]["mode"] == "as_written"


async def test_a_draft_the_answer_key_wrote_is_read_again_by_its_key(monkeypatch):
    seen = []

    async def run(key, params, org, *, question=""):
        seen.append((key.id, params, org, question))
        return {"found": True, "facts": [{"id": "f1", "label": "Blocked vendors", "value": 2, "receipt": "q1"}], "receipts": [],
                "read_at": "t", "plan": {}, "notes": ["n"]}
    monkeypatch.setattr(ak, "run", run)
    _turn(monkeypatch, None, kind="answer_key", text="Which vendors are blocked?",
          more={"expected": {"evidence": {"drafted_from": "answer_key", "key": "vendors_blocked", "params": {}}}})
    out = await ak.recheck(member(), "turn-1", index=0)
    assert seen == [("vendors_blocked", {}, ORG, "Which vendors are blocked?")]
    assert out["sentence"] == "2 vendors are blocked." and out["notes"] == ["n"] and out["found"] is True


@pytest.mark.parametrize("error,reason", [
    (records_run.NotReady("no map"), "not_ready"),
    (ValueError("the plan does not fit this database: Floor has no state 'x'"), "plan_does_not_fit"),
    (KeyError("focus"), "plan_does_not_fit"),
    (RuntimeError("the connection was closed"), "read_failed"),
])
async def test_a_read_that_cannot_be_made_says_why_in_words(reads, monkeypatch, error, reason):
    _turn(monkeypatch, [_block()])
    reads.fail = error
    with pytest.raises(Invalid) as e:
        await ak.recheck(member(), "turn-1", index=0)
    assert e.value.reason == reason and e.value.error and "connection was closed" not in e.value.error


async def test_a_query_read_as_its_asker_is_read_again_with_that_persons_access(reads, monkeypatch):
    seen = _no_user(monkeypatch)
    asked = []

    async def asker(org, user_id):
        asked.append((org, user_id))
        return {"scope": "asker", "building_ids": ["b1", "b2"], "email": "asker@example.com"}
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    _turn(monkeypatch, [_block(scope="asker", scope_email="asker@example.com")])
    out = await ak.recheck(member(), "turn-7", index=0)
    assert seen == [("turn-7", ORG)] and asked == [(ORG, "u-1")], "the person on the turn, in the question's company"
    assert reads.calls[0]["scope"] == {"building_ids": ["b1", "b2"], "email": "asker@example.com"}
    assert out["scope"] == "asker" and out["scope_email"] == "asker@example.com"


async def test_a_stored_query_that_does_not_say_how_it_was_read_is_read_as_its_asker(reads, monkeypatch):
    """A query a person saved from the editor may carry no scope: never more than the asker could see."""
    _no_user(monkeypatch)

    async def asker(org, user_id):
        return {"scope": "asker", "building_ids": ["b1"], "email": "asker@example.com"}
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    block = _block()
    block.pop("scope"), block.pop("scope_email")
    _turn(monkeypatch, [block])
    out = await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls[0]["scope"] == {"building_ids": ["b1"], "email": "asker@example.com"} and out["scope"] == "asker"


async def test_an_administrator_asker_is_read_for_every_building_as_themselves(reads, monkeypatch):
    _no_user(monkeypatch)

    async def asker(org, user_id):
        return {"scope": "asker", "building_ids": None, "email": "boss@example.com"}
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    _turn(monkeypatch, [_block(scope="asker")])
    await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls[0]["scope"] == {"building_ids": None, "email": "boss@example.com"}


async def test_an_asker_who_can_no_longer_be_rebuilt_is_not_read_as_the_whole_company_under_the_old_label(reads, monkeypatch):
    _no_user(monkeypatch)

    async def asker(org, user_id):
        return {"scope": "company", "building_ids": None, "email": None}
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    _turn(monkeypatch, [_block(scope="asker", scope_email="asker@example.com")])
    out = await ak.recheck(member(), "turn-1", index=0)
    assert reads.calls[0]["scope"] is None and out["scope"] == "company" and out["scope_email"] is None, "the page is told"


@pytest.mark.parametrize("scope,reason", [({"scope": "asker", "building_ids": [], "email": "n@example.com"}, "asker_has_no_buildings"),
                                          (auto_expected.AskerUnreadable("the accounts are down"), "asker_unreadable")])
async def test_an_asker_with_no_buildings_or_who_cannot_be_read_is_not_read_for(reads, monkeypatch, scope, reason):
    _no_user(monkeypatch)

    async def asker(org, user_id):
        if isinstance(scope, Exception):
            raise scope
        return scope
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    _turn(monkeypatch, [_block(scope="asker")])
    with pytest.raises(Invalid) as e:
        await ak.recheck(member(), "turn-1", index=0)
    assert e.value.reason == reason and reads.calls == []


# ── The query as fields' replacement: GET and POST /expected/query ──

def _kind_tree(monkeypatch, nodes=None):
    async def tree(m, turn_id):
        return {"nodes": nodes if nodes is not None else [{"id": "n1"}]}
    monkeypatch.setattr(eval_store, "trace_tree_of", tree)


async def test_the_editor_starts_from_the_stored_query_as_run_with_where_it_came_from(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62, "How many floors does ABC have?"), _block(_plan(_node("Building")), "And buildings?", source="proposed")])
    got = await ak.orchestrator_query(member(), "turn-1")
    assert got["ready"] is True and got["can_run"] is True and got["none"] is None
    assert [(q["index"], q["source"], q["source_words"], q["question"]) for q in got["queries"]] == [
        (0, "hoistra", records_queries.SOURCE_WORDS["hoistra"], "How many floors does ABC have?"),
        (1, "proposed", "Proposed for this question - check it before accepting", "And buildings?")]
    assert got["queries"][0]["plan"] == R62
    assert {c["name"] for c in got["map"]["concepts"]} == {"Building", "Floor", "ComplianceCertificate", "Vendor"}
    assert reads.calls == [], "showing the editor reads no records"


async def test_a_query_a_person_saved_says_so(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62, source="person")])
    got = await ak.orchestrator_query(member(), "turn-1")
    assert got["queries"][0]["source"] == "person" and got["queries"][0]["source_words"] == "Your version of the query"


async def test_a_person_who_may_not_run_queries_is_told_so(reads, monkeypatch):
    _turn(monkeypatch, [_block()])
    got = await ak.orchestrator_query(member(role="reviewer", all_companies=False, manages=False), "turn-1")
    assert got["can_run"] is False and got["queries"], "they still see the query"


async def test_without_a_draft_the_turns_own_queries_are_read_from_its_trace(reads, monkeypatch):
    _turn(monkeypatch, None, kind=None)
    _kind_tree(monkeypatch, [{"id": "n1"}])
    asked = []

    async def queries_for(nodes, question, *, propose):
        asked.append((nodes, question, propose))
        return {"queries": [{"source": "answer_key", "plan": R62, "question": question}], "none": None}
    monkeypatch.setattr(records_queries, "queries_for", queries_for)
    got = await ak.orchestrator_query(member(), "turn-1")
    assert asked == [([{"id": "n1"}], "How many floors does ABC have?", False)], "never a model call from here"
    assert got["queries"] == [{"index": 0, "source": "answer_key", "source_words": records_queries.SOURCE_WORDS["answer_key"],
                               "question": "How many floors does ABC have?", "plan": R62}]


async def test_a_turn_with_no_query_says_why(reads, monkeypatch):
    _turn(monkeypatch, None, kind="spec", more={"expected": {"evidence": {"drafted_from": "SPEC.md", "no_query": "not_about_records"}}})
    _kind_tree(monkeypatch)

    async def queries_for(nodes, question, *, propose):
        return {"queries": [], "none": None}
    monkeypatch.setattr(records_queries, "queries_for", queries_for)
    got = await ak.orchestrator_query(member(), "turn-1")
    assert got["queries"] == [] and got["none"] == "not_about_records" and got["ready"] is True


async def test_before_the_map_is_built_the_editor_waits_but_the_queries_show(reads, monkeypatch):
    async def not_built():
        raise records_run.NotReady("no map")
    monkeypatch.setattr(records_run, "load_map", not_built)
    _turn(monkeypatch, [_block()])
    got = await ak.orchestrator_query(member(), "turn-1")
    assert got["ready"] is False and got["map"] is None and len(got["queries"]) == 1


async def test_only_a_live_questions_query_is_shown(reads, monkeypatch):
    _turn(monkeypatch, [_block()], live=False)
    with pytest.raises(Invalid) as e:
        await ak.orchestrator_query(member(), "turn-1")
    assert e.value.reason == "not_live"


async def test_another_companys_turn_has_no_editor(reads, monkeypatch):
    async def not_found(m, turn_id):
        raise NotFound("turn_not_found", "That turn is not in Trace Lab.")
    monkeypatch.setattr(runs, "turn_expected", not_found)
    other = member(org=OTHER_ORG, all_companies=False, role="admin")
    with pytest.raises(NotFound):
        await ak.orchestrator_query(other, "turn-1")
    with pytest.raises(NotFound):
        await ak.try_query(other, "turn-1", index=0, plan=R62)
    assert reads.calls == []


def _changed_plan(concept="Building"):
    new = json.loads(json.dumps(R62))
    new["focus"]["concept"] = concept
    new["constraints"] = []
    return new


async def test_a_changed_plan_gives_what_hoistras_query_finds_beside_what_this_one_finds(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62, "How many floors does ABC have?")])
    reads.totals = {"Floor": 0, "Building": 4}
    new = _changed_plan("Building")
    got = await ak.try_query(member(), "turn-1", index=0, plan=new)
    before, after = reads.calls
    assert before == {"plan": R62, "question": "How many floors does ABC have?", "org": ORG, "mode": "hoistra", "scope": None}
    assert after == {"plan": new, "question": "How many floors does ABC have?", "org": ORG, "mode": "as_written", "scope": None}, \
        "the person's plan runs as written: what they see is what runs"
    assert got["before"]["facts"][0]["value"] == 0 and got["after"]["facts"][0]["value"] == 4
    assert got["before"]["facts"][0]["label"] == "Floor records found" and got["after"]["facts"][0]["label"] == "Building records found"
    assert got["changes"] == ["What: Floor records → Building records", "Limited to: Building ABC removed"]
    assert set(got["after"]) >= {"found", "facts", "receipts", "read_at", "unresolved", "notes", "sentence"}
    assert got["after"]["sentence"] == "Building records found: 4." and got["before"]["sentence"] == "Floor records found: 0."


async def test_a_query_a_person_saved_is_the_before_side_as_they_wrote_it(reads, monkeypatch):
    _turn(monkeypatch, [_block(R62, source="person")])
    await ak.try_query(member(), "turn-1", index=0, plan=_changed_plan("Building"))
    assert [c["mode"] for c in reads.calls] == ["as_written", "as_written"]


async def test_only_a_curator_or_the_platform_team_may_run_a_plan(reads, monkeypatch):
    async def never(m, turn_id):
        raise AssertionError("a person who may not run a query must be refused before anything is looked up")
    monkeypatch.setattr(runs, "turn_expected", never)
    with pytest.raises(Forbidden):
        await ak.try_query(member(role="reviewer", all_companies=False, manages=False), "turn-1", index=0, plan=R62)
    assert reads.calls == []


async def test_a_plan_the_map_refuses_is_refused_in_words_and_nothing_is_read(reads, monkeypatch):
    _turn(monkeypatch, [_block()])
    bad = _plan(_node("Flor"))
    with pytest.raises(Invalid) as e:
        await ak.try_query(member(), "turn-1", index=0, plan=bad)
    assert e.value.reason == "bad_query" and "'Flor' is not a kind of record" in e.value.error and reads.calls == []
    assert e.value.extra["problems"] == ["What: 'Flor' is not a kind of record Hoistra knows."], "the page lists them"


async def test_a_query_that_is_not_in_the_turn_is_refused(reads, monkeypatch):
    _turn(monkeypatch, [_block()])
    for index in (1, -1, "x", None):
        with pytest.raises(Invalid) as e:
            await ak.try_query(member(), "turn-1", index=index, plan=R62)
        assert e.value.reason == "no_query"
    assert reads.calls == []


async def test_before_the_map_is_built_a_plan_cannot_be_run(reads, monkeypatch):
    async def not_built():
        raise records_run.NotReady("no map")
    monkeypatch.setattr(records_run, "load_map", not_built)
    _turn(monkeypatch, [_block()])
    with pytest.raises(Invalid) as e:
        await ak.try_query(member(), "turn-1", index=0, plan=R62)
    assert e.value.reason == "not_ready"


async def test_the_records_refusing_the_persons_plan_is_theirs_to_fix_not_a_crash(reads, monkeypatch):
    _turn(monkeypatch, [_block()])
    reads.fail, reads.fail_mode = RuntimeError("canceling statement due to statement timeout"), "as_written"
    with pytest.raises(Invalid) as e:
        await ak.try_query(member(), "turn-1", index=0, plan=_changed_plan("Floor"))
    assert e.value.reason == "bad_query" and "statement timeout" in e.value.error
    assert e.value.extra["problems"] == [e.value.error]


async def test_hoistras_own_query_not_reading_does_not_stop_the_persons_from_running(reads, monkeypatch):
    _turn(monkeypatch, [_block()])
    reads.fail, reads.fail_mode = RuntimeError("the records did not answer"), "hoistra"
    got = await ak.try_query(member(), "turn-1", index=0, plan=_changed_plan("Building"))
    assert got["after"]["found"] is True and got["after"]["facts"]
    assert got["before"]["found"] is False and got["before"]["facts"] == [] and got["before"]["error"]


async def test_both_sides_are_read_with_the_askers_access_unless_the_query_was_read_for_the_company(reads, monkeypatch):
    _no_user(monkeypatch)

    async def asker(org, user_id):
        return {"scope": "asker", "building_ids": ["b1"], "email": "asker@example.com"}
    monkeypatch.setattr(auto_expected, "asker_scope", asker)
    _turn(monkeypatch, [_block(scope="asker", scope_email="asker@example.com")])
    got = await ak.try_query(member(), "turn-1", index=0, plan=_changed_plan("Building"))
    assert [c["scope"] for c in reads.calls] == [{"building_ids": ["b1"], "email": "asker@example.com"}] * 2
    assert got["scope"] == "asker" and got["scope_email"] == "asker@example.com"
    reads.calls.clear()
    _turn(monkeypatch, [_block(scope="company")])
    got = await ak.try_query(member(), "turn-1", index=0, plan=_changed_plan("Building"))
    assert [c["scope"] for c in reads.calls] == [None, None] and got["scope"] == "company"


# ── The routes: the path, the query string and the body reach the right function ──

@pytest.fixture
def http(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from trace_lab import db, routes_runs

    who = member(org=ORG, all_companies=False, role="admin")
    app = FastAPI()
    app.include_router(routes_runs.router, prefix="/api/trace-lab")

    async def _member():
        return who

    async def _ready():
        return None

    app.dependency_overrides[routes_runs._wide] = _member
    app.dependency_overrides[db.require_ready] = _ready
    calls = []

    def fake(name, result):
        async def call(*args, **kwargs):
            calls.append((name, args[1:], kwargs))
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(ak, name, call)

    client = TestClient(app)
    client.calls, client.fake, client.who = calls, fake, who
    return client


U = "/api/trace-lab/lab/turns/turn-1/expected"


def test_run_again_takes_the_query_in_the_query_string(http):
    http.fake("recheck", {"found": True, "facts": []})
    assert http.post(U + "/check").json() == {"ok": True, "found": True, "facts": []}
    assert http.post(U + "/check?index=2").status_code == 200
    assert [c[2] for c in http.calls] == [{"index": 0}, {"index": 2}] and http.calls[0][1] == ("turn-1",)
    for bad in ("-1", "x", "1001"):
        assert http.post(U + "/check?index=" + bad).status_code == 422


def test_the_editor_is_read_with_get_and_run_with_a_post_of_the_plan(http):
    http.fake("orchestrator_query", {"ready": True, "can_run": True, "queries": [], "none": None, "map": None})
    assert http.get(U + "/query").json()["ok"] is True and http.calls[-1][0] == "orchestrator_query"
    http.fake("try_query", {"before": {}, "after": {}, "changes": []})
    got = http.post(U + "/query", json={"index": 1, "plan": R62})
    assert got.status_code == 200 and got.json() == {"ok": True, "before": {}, "after": {}, "changes": []}
    assert http.calls[-1] == ("try_query", ("turn-1",), {"index": 1, "plan": R62})
    http.post(U + "/query", json={"plan": R62})
    assert http.calls[-1][2]["index"] == 0, "the first query unless told another"
    sent = len(http.calls)
    for bad in ({}, {"index": 0}, {"index": -1, "plan": R62}, {"index": 0, "plan": "Floor"}, {"index": 0, "plan": [R62]}):
        assert http.post(U + "/query", json=bad).status_code == 422, bad
    assert len(http.calls) == sent, "nothing runs for a request that is not a plan"


def test_a_refusal_comes_back_in_the_house_envelope_with_its_words(http):
    http.fake("try_query", Invalid("bad_query", "What: 'Flor' is not a kind of record Hoistra knows."))
    r = http.post(U + "/query", json={"index": 0, "plan": R62})
    assert r.status_code == 422 and r.json()["detail"] == {"reason": "bad_query", "error": "What: 'Flor' is not a kind of record Hoistra knows."}
    http.fake("try_query", Forbidden("curators_only", "Only company admins, the platform team and the consultant can do this."))
    assert http.post(U + "/query", json={"index": 0, "plan": R62}).status_code == 403
    http.fake("orchestrator_query", NotFound("turn_not_found", "That turn is not in Trace Lab."))
    assert http.get(U + "/query").status_code == 404
