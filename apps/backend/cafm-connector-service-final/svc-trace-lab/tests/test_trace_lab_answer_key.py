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


# ── Correcting the orchestrator's own query (16 Nov 2026, Hussain): "if I ask which certificates expire in 60 days the
# user must be able to change the date in the query if the query by the orchestrator is wrong and see the correct
# answer". The plan the orchestrator's records tool used is read from its trace; a person changes the states, the
# building or the dates in plain fields; the corrected plan is compiled by the same engine and read. Only the expected
# answer is corrected - the orchestrator is never changed. ────────────────────────────────────────────────────────

import json
from types import SimpleNamespace

PLAN = {"question_type": "list",
        "focus": {"concept": "ComplianceCertificate", "match": None, "states": ["expiring_soon"],
                  "filters": [{"attribute": "expiry_date", "op": "within", "value": "next_30_days"}]},
        "constraints": [{"concept": "Building", "match": "Bishopsgate", "via": [], "states": [], "filters": []}],
        "include": [], "aggregate": None}


def _onto():
    cert = SimpleNamespace(name="ComplianceCertificate", label="inspector_name", identifiers=["certificate_ref"],
                           attributes={"id": "id", "certificate_ref": "certificate_ref", "expiry_date": "expiry_date",
                                       "issue_date": "issue_date", "status": "status", "next_due_date": "next_due_date"},
                           states={"lapsed": {"description": "lapsed / expired"}, "expiring_soon": {"description": "status = Expiring soon"}})
    return SimpleNamespace(concepts={"ComplianceCertificate": cert})


def test_the_orchestrators_plans_are_read_from_its_trace():
    nodes = [{"type": "llm", "output": {"content": "", "tool_calls": [{"name": "answer_from_records"}]}},
             {"type": "llm", "output": {"content": "not json"}},
             {"type": "llm", "output": {"content": json.dumps(PLAN)}},
             {"type": "tool", "output": {"plan": "no"}},
             {"type": "llm", "output": {"content": "```json\n" + json.dumps(PLAN) + "\n```"}}]
    plans = ak.extract_plans(nodes)
    assert len(plans) == 1 and plans[0]["focus"]["concept"] == "ComplianceCertificate", "the same plan once"
    assert ak.extract_plans([]) == []


def test_the_plan_as_plain_fields():
    f = ak.plan_fields(_onto(), PLAN)
    assert f["concept"] == "ComplianceCertificate" and f["what"] == "compliance certificates"
    assert f["states"] == ["expiring_soon"]
    assert [c["id"] for c in f["state_choices"]] == ["lapsed", "expiring_soon"]
    assert f["building"] == "Bishopsgate"
    assert f["dates"] == [{"attribute": "expiry_date", "op": "within", "value": "next_30_days"}]
    assert f["date_attributes"] == ["expiry_date", "issue_date", "next_due_date"]


def test_a_persons_changes_make_a_new_plan_and_say_what_changed():
    fields = {"states": ["expiring_soon"], "building": "",
              "dates": [{"attribute": "expiry_date", "op": "within", "value": "next_60_days"}]}
    plan, changes = ak.apply_fields(_onto(), PLAN, fields)
    assert plan["focus"]["filters"] == [{"attribute": "expiry_date", "op": "within", "value": "next_60_days"}]
    assert [(c["concept"], c["match"]) for c in plan["constraints"]] == [("Building", None)], "building cleared: every building"
    assert plan["focus"]["concept"] == "ComplianceCertificate" and plan is not PLAN and PLAN["constraints"], "the original is untouched"
    assert changes == ["Building: Bishopsgate → every building", "expiry_date: within next_30_days → within next_60_days"]


def test_dates_can_be_a_range_and_bad_changes_are_refused():
    plan, changes = ak.apply_fields(_onto(), PLAN, {"states": ["lapsed"], "building": "Bishopsgate",
                                                    "dates": [{"attribute": "expiry_date", "op": "between", "value": ["2026-11-16", "2027-01-15"]}]})
    assert plan["focus"]["states"] == ["lapsed"]
    assert plan["focus"]["filters"][0]["value"] == ["2026-11-16", "2027-01-15"]
    assert changes[0] == "States: expiring_soon → lapsed"
    for bad in ({"states": ["made_up"]}, {"dates": [{"attribute": "password", "op": "within", "value": "next_5_days"}]},
                {"dates": [{"attribute": "expiry_date", "op": "drop", "value": "x"}]},
                {"dates": [{"attribute": "expiry_date", "op": "between", "value": ["2026-13-01", "2027-01-01"]}]},
                {"dates": [{"attribute": "expiry_date", "op": "within", "value": "next_99999_days"}]}):
        with pytest.raises(ValueError):
            ak.apply_fields(_onto(), PLAN, {**{"states": ["expiring_soon"], "building": "", "dates": []}, **bad})


def test_any_records_answer_becomes_values_and_a_sentence():
    result = {"kind": "records", "focus": "ComplianceCertificate", "total": 2,
              "records": [{"attributes": {"certificate_ref": "LOLER-B-301-4471", "inspector_name": "A. Smith"}},
                          {"attributes": {"certificate_ref": "FAS-B-301-2026-03"}}]}
    receipts = [{"id": "q1", "label": "count"}, {"id": "q2", "label": "focus ComplianceCertificate"}]
    facts = ak.generic_facts(_onto(), PLAN, result, receipts)
    assert facts[0] == {"id": "f1", "label": "Compliance certificates found", "value": 2, "receipt": "q1"}
    assert [f["value"] for f in facts[1:]] == ["LOLER-B-301-4471", "FAS-B-301-2026-03"]
    assert ak.generic_sentence(_onto(), PLAN, facts) == "2 compliance certificates: LOLER-B-301-4471, FAS-B-301-2026-03."
    none = ak.generic_facts(_onto(), PLAN, {"kind": "records", "focus": "ComplianceCertificate", "total": 0, "records": []}, receipts)
    assert ak.generic_sentence(_onto(), PLAN, none) == "No compliance certificates."


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


async def test_when_hoistra_used_another_tool_the_answer_keys_query_is_the_starting_point(monkeypatch):
    from trace_lab import eval_store, runs

    async def turn(m, turn_id):
        return {"live": True, "company": "org-1", "text": "Which certificates expire in the next 30 days?"}

    async def tree(m, turn_id):
        return {"nodes": [{"output": {"content": "", "tool_calls": [{"name": "list_building_certificates"}]}}]}
    monkeypatch.setattr(runs, "turn_expected", turn)
    monkeypatch.setattr(eval_store, "trace_tree_of", tree)
    got, plans, source = await ak._turn_plans(None, "t-1")
    assert source == "answer_key" and plans[0]["focus"]["filters"][0]["value"] == "next_30_days"

    async def plain(m, turn_id):
        return {"live": True, "company": "org-1", "text": "tell me more about bishopsgate tower"}
    monkeypatch.setattr(runs, "turn_expected", plain)
    assert (await ak._turn_plans(None, "t-1"))[1:] == ([], None)


# ── Review fixes before commit (16 Nov 2026) ──

@pytest.mark.parametrize("value", [["next_30_days"], "next_0_days", None, 30])
def test_a_period_that_is_not_one_is_refused_not_crashed(value):
    with pytest.raises(ValueError):
        ak._check_date({"attribute": "expiry_date", "op": "within", "value": value}, ["expiry_date"])


def test_the_plan_that_answered_comes_first():
    first = {"focus": {"concept": "WorkOrder", "states": ["open"]}}
    last = {"focus": {"concept": "WorkOrder", "states": ["overdue"]}}
    assert ak.used_first([first, last]) == [last, first], "a retried plan replaced the first; the last one ran"


@pytest.mark.parametrize("question", ["How many open work orders are in progress at the moment?",
                                      "open work orders for HVAC"])
def test_words_that_are_not_buildings_are_not_read_as_one(question):
    assert ak._place(question) is None
