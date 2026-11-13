"""The plan says which records each step reads, and a read the data cannot join is refused when the
plan is made (13 Nov 2026).

"Why is my orchestrator's planning not planning which table's data needs to be got?" Read from the traces
and the code: a plan was written in engines and tools only; the tables were chosen below it, inside a tool
or by the record tool's own small planning call, where the plan could neither see nor check the choice;
and the planner did not run at all for 46 of the first 54 turns.

  * The question that showed it - "Which vendors are blocked at Bishopsgate Tower, and do any of them
    have open work orders?" (turn-65d8787aac9741aa) - was not read as two parts, and a keyword dropped
    the second area the router had read. One sub-agent made one read, joined vendors to work orders
    through a column no row fills, and the answer was "none". Apex Mechanical had three.
  * The planner is now given the record kinds this database holds, their tables and which are linked
    by a link rows carry; each step lists the kinds it reads; one read of two kinds no row links is
    refused and planned once more as two steps.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from src.agents import agent_router, ontology_qa, planner, trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

TRACE_QUESTION = "Which vendors are blocked at Bishopsgate Tower, and do any of them have open work orders?"
TOOLS = {"answer_from_records": "counts, lists or details of records", "list_blocked_vendors": "blocked vendors",
         "work_order_blockers": "work orders blocked by a vendor"}


# -- 1. the planner runs when it should -----------------------------------------------------------

def test_a_second_question_asked_with_a_verb_first_is_a_second_part():
    assert planner.is_multi_part(TRACE_QUESTION)
    assert planner.is_multi_part("Which certificates have lapsed at Kingsway House, and is any work order raised for them?")
    assert planner.is_multi_part("List the blocked vendors and does each of those have a contract ending this year?")


def test_one_question_with_an_and_in_it_is_still_one_part():
    assert not planner.is_multi_part("Which vendors are blocked and at risk at Bishopsgate Tower?")
    assert not planner.is_multi_part("List the lifts and escalators at Bishopsgate Tower")
    assert not planner.is_multi_part("Which vendors are active and are approved for lift work?")   # "and are <adjective>"


def test_a_question_the_router_gave_to_two_areas_is_planned_whatever_its_wording():
    one = {"agent": "compliance", "also": []}
    two = {"agent": "compliance", "also": ["wo_engine"]}
    assert planner.needs_plan("Blocked vendors with their open jobs at Bishopsgate Tower", two)
    assert not planner.needs_plan("Blocked vendors with their open jobs at Bishopsgate Tower", one)
    assert not planner.needs_plan("Blocked vendors at Bishopsgate Tower", {"agent": "compliance", "also": ["compliance"]})
    assert not planner.needs_plan("How many boilers at B-301?", None)
    assert planner.needs_plan(TRACE_QUESTION, one)                     # its wording alone is enough now


def test_both_entry_points_ask_whether_the_turn_needs_a_plan():
    import inspect

    import src.agents.orchestrator as O

    src = inspect.getsource(O)
    assert src.count("planner.needs_plan(user_message, routing)") == 2
    assert "planner.is_multi_part(user_message)" not in src


def test_the_area_the_router_read_stays_in_the_turn_when_a_keyword_names_another():
    """It was read as compliance and also wo_engine; "work orders" made wo_engine first and compliance was dropped."""
    known = {"compliance", "wo_engine", "contract_performance", "energy_intelligence"}
    assert agent_router.areas_after_override("compliance", ["wo_engine"], "wo_engine", known) == ("wo_engine", ["compliance"])
    assert agent_router.areas_after_override("compliance", [], "wo_engine", known) == ("wo_engine", ["compliance"])
    # nothing overrode it: as the router said, and never an area beside itself
    assert agent_router.areas_after_override("compliance", ["wo_engine"], None, known) == ("compliance", ["wo_engine"])
    assert agent_router.areas_after_override("compliance", ["compliance", "wo_engine"], "compliance", known) == ("compliance", ["wo_engine"])
    assert agent_router.areas_after_override("somewhere_else", [], "wo_engine", known) == ("wo_engine", [])


# -- 2. the planner is shown the records and their links ---------------------------------------------

def _onto(*, assigned_fill=0.0, vendor_fill=1.0, with_vendor_id=True):
    """Vendors and work orders as on hoistra_test: two links, one of them empty on every row."""
    rel = {
        "WORK_ORDER_ASSIGNED_VENDOR": {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                                       "join": ["from.assigned_vendor = to.id"], "fill": assigned_fill},
        "WORK_ORDER_BUILDING": {"from": "WorkOrder", "to": "Building", "cardinality": "many_to_one",
                                "join": ["from.building_id = to.building_id"], "fill": 1.0},
        "CERTIFICATE_VENDOR": {"from": "ComplianceCertificate", "to": "Vendor", "cardinality": "many_to_one",
                               "join": ["from.vendor_id = to.id"], "fill": 0.6},
    }
    if with_vendor_id:
        rel["WORK_ORDER_VENDOR"] = {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                                    "join": ["from.vendor_id = to.id"], "fill": vendor_fill}
    return ontology_qa.Ontology({
        "schema": "plenum_scoped",
        "concepts": {
            "Vendor": {"table": "vendors", "key": "id", "label": "vendor_name", "synonyms": ["contractor", "supplier"],
                       "attributes": {"id": "id", "vendor_name": "vendor_name", "block_state": "block_state"},
                       "states": {"blocked": {"attribute": "block_state", "in": ["Blocked"]}}},
            "WorkOrder": {"table": "work_orders", "key": "id", "label": "title",
                          "attributes": {"id": "id", "title": "title", "status": "status"},
                          "states": {"open": {"attribute": "status", "not_in": ["Completed", "Closed"]}}},
            "Building": {"table": "buildings", "key": "building_id", "label": "name", "hub": True,
                         "attributes": {"building_id": "building_id", "name": "name"}},
            "ComplianceCertificate": {"table": "compliance_certificates", "key": "id", "label": "certificate_number",
                                      "synonyms": ["certificate", "accreditation"],
                                      "attributes": {"id": "id", "certificate_number": "certificate_number", "status": "status"}},
            "Meter": {"table": "meters", "key": "id", "label": "name", "attributes": {"id": "id", "name": "name"}},
        },
        "relations": rel,
        "derived": {},
    })


def test_a_kind_is_known_by_its_name_its_table_or_the_word_people_use():
    m = planner.RecordsMap(_onto())
    for said in ("Vendor", "vendors", "VENDOR", "contractor", "Suppliers", "work_orders", "Work Orders", "WorkOrder", "workorders"):
        assert m.kind(said) in ("Vendor", "WorkOrder"), said
    assert m.kind("certificates") == "ComplianceCertificate" and m.table("ComplianceCertificate") == "compliance_certificates"
    assert m.kind("payroll") is None


def test_two_kinds_are_linked_only_by_a_link_rows_carry():
    assert planner.RecordsMap(_onto()).linked("Vendor", "WorkOrder")[0] is True          # vendor_id is filled
    ok, why = planner.RecordsMap(_onto(with_vendor_id=False)).linked("Vendor", "WorkOrder")
    assert ok is False and "WORK_ORDER_ASSIGNED_VENDOR" in why and "empty on every record" in why
    ok, why = planner.RecordsMap(_onto()).linked("Vendor", "Meter")
    assert ok is False and why == "nothing links them"
    assert planner.RecordsMap(_onto()).linked("Vendor", "Vendor") == (True, "the same kind")


def test_a_link_whose_share_was_never_measured_is_taken_on_trust():
    """A relation written by hand carries no measured share: 0 for it means "not measured", never "empty"."""
    o = _onto(with_vendor_id=False)
    o.relations["WORK_ORDER_ASSIGNED_VENDOR"].fill_known = False
    assert planner.RecordsMap(o).linked("Vendor", "WorkOrder")[0] is True
    rel = ontology_qa.Relation("X", {"from": "A", "to": "B", "join": ["from.a = to.b"]})
    assert rel.fill_known is False and ontology_qa.Relation("Y", {"from": "A", "to": "B", "join": ["from.a = to.b"], "fill": 0.0}).fill_known


def test_the_planner_is_shown_each_kind_its_table_and_what_it_is_linked_to():
    text = planner.RecordsMap(_onto(with_vendor_id=False)).describe(TRACE_QUESTION)
    vendor = next(line for line in text.splitlines() if line.startswith("- Vendor "))
    assert "(table vendors)" in vendor and "states blocked" in vendor
    assert "linked to: ComplianceCertificate" in vendor and "no rows link it to: WorkOrder" in vendor
    assert "- Meter (table meters) | linked to: nothing" in text
    filled = planner.RecordsMap(_onto()).describe(TRACE_QUESTION)
    assert "no rows link it to" not in next(line for line in filled.splitlines() if line.startswith("- Vendor "))


def test_before_the_record_model_is_built_there_is_no_map_and_no_waiting(monkeypatch):
    monkeypatch.setattr(ontology_qa, "_ONTOLOGY", {})
    assert ontology_qa.built_ontology() is None and planner.records_map() is None
    built = _onto()
    monkeypatch.setattr(ontology_qa, "_ONTOLOGY", {"an-older-schema": _onto(with_vendor_id=False), "this-schema": built})
    assert ontology_qa.built_ontology() is built and planner.records_map().onto is built


# -- 3. the data plan is checked before anything runs -------------------------------------------------

def _plan(*steps):
    return {"goal": "g", "steps": list(steps), "answer_shape": "a table"}


ONE_READ = {"id": "s1", "kind": "tool", "target": "answer_from_records", "records": ["vendors", "work orders"],
            "ask": "the blocked vendors at Bishopsgate Tower with their open work orders", "args": {"question": "x"}}


def test_a_step_names_its_record_kinds_and_tables():
    plan, why = planner.validate(_plan(ONE_READ), TOOLS, None, planner.RecordsMap(_onto()))
    assert why is None
    assert plan["steps"][0]["records"] == ["Vendor", "WorkOrder"]
    assert plan["steps"][0]["tables"] == ["vendors", "work_orders"]


def test_one_read_of_two_kinds_no_row_links_is_refused_when_the_plan_is_made():
    plan, why = planner.validate(_plan(ONE_READ), TOOLS, None, planner.RecordsMap(_onto(with_vendor_id=False)))
    assert plan is None
    assert why.startswith("step s1: Vendor and WorkOrder cannot be read together")
    assert "empty on every record" in why and "$<step id>.<field>" in why


def test_the_same_two_kinds_in_two_steps_are_accepted():
    two = _plan({"id": "s1", "kind": "tool", "target": "list_blocked_vendors", "records": ["Vendor"],
                 "ask": "the blocked vendors at Bishopsgate Tower", "args": {"building_name": "Bishopsgate Tower"}},
                {"id": "s2", "kind": "tool", "target": "answer_from_records", "records": ["WorkOrder"],
                 "ask": "open work orders of the vendors from s1", "args": {"question": "x"}, "depends_on": ["s1"]})
    plan, why = planner.validate(two, TOOLS, None, planner.RecordsMap(_onto(with_vendor_id=False)))
    assert why is None and [s["records"] for s in plan["steps"]] == [["Vendor"], ["WorkOrder"]]


def test_a_kind_the_map_does_not_know_is_dropped_not_refused():
    step = dict(ONE_READ, records=["vendors", "payroll runs"])
    plan, why = planner.validate(_plan(step), TOOLS, None, planner.RecordsMap(_onto()))
    assert why is None and plan["steps"][0]["records"] == ["Vendor"]


def test_an_engine_step_lists_its_kinds_and_is_not_held_to_one_read():
    step = {"id": "s1", "kind": "engine", "target": "compliance", "records": ["Vendor", "Meter"], "ask": "which vendors are blocked"}
    plan, why = planner.validate(_plan(step), TOOLS, None, planner.RecordsMap(_onto()))
    assert why is None and plan["steps"][0]["records"] == ["Vendor", "Meter"]


def test_without_a_map_a_plan_is_checked_as_it_always_was():
    plan, why = planner.validate(_plan(ONE_READ), TOOLS)
    assert why is None and plan["steps"][0]["records"] == ["vendors", "work orders"] and "tables" not in plan["steps"][0]
    bare = {k: v for k, v in ONE_READ.items() if k != "records"}
    assert "records" not in planner.validate(_plan(bare), TOOLS)[0]["steps"][0]


def test_a_refused_read_is_planned_once_more_as_two_steps():
    asked: list[str] = []
    replies = [json.dumps(_plan(ONE_READ)),
               json.dumps(_plan({"id": "s1", "kind": "tool", "target": "list_blocked_vendors", "records": ["Vendor"],
                                 "ask": "the blocked vendors at Bishopsgate Tower", "args": {}},
                                {"id": "s2", "kind": "tool", "target": "answer_from_records", "records": ["WorkOrder"],
                                 "ask": "open work orders of the vendors from s1", "args": {"question": "x"}, "depends_on": ["s1"]}))]

    async def llm(system, user, role):
        asked.append(user)
        return replies[len(asked) - 1]

    data = planner.RecordsMap(_onto(with_vendor_id=False))
    plan, why = asyncio.run(planner.make_plan(TRACE_QUESTION, context="", tools=TOOLS, llm=llm, data=data))
    assert why is None and len(plan["steps"]) == 2 and len(asked) == 2
    assert "RECORDS (the kinds of record held here" in asked[0] and "no rows link it to: WorkOrder" in asked[0]
    assert "In every step give `records`" in asked[0]
    assert "YOUR PREVIOUS PLAN WAS REFUSED: step s1: Vendor and WorkOrder cannot be read together" in asked[1]


def test_a_plan_made_without_the_map_reads_as_before():
    asked: list[str] = []

    async def llm(system, user, role):
        asked.append(user)
        return json.dumps(_plan({k: v for k, v in ONE_READ.items() if k != "records"}))

    plan, why = asyncio.run(planner.make_plan("How many boilers are there at B-301 and B-302?", context="", tools=TOOLS,
                                              llm=llm, data=False))
    assert why is None and "RECORDS (the kinds" not in asked[0] and "In every step give `records`" not in asked[0]


# -- and the trace shows what each step read -------------------------------------------------------------

@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    async def fake_write(t, answer, tool_calls, summary):
        return None

    monkeypatch.setattr(trace, "_write", fake_write)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    trace._current_step.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q",
                       "payload": {"message": "q"}})
    yield trace.current()
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)


def test_each_steps_span_says_which_records_and_tables_it_read(traced):
    plan, _ = planner.validate(_plan(ONE_READ), TOOLS, None, planner.RecordsMap(_onto()))

    async def run_tool(name, args):
        return {"ok": True, "records": [{"vendor_name": "Apex Mechanical"}]}

    async def run_engine(*a):
        raise AssertionError

    asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    step = next(s for s in traced.spans if s.kind == "step" and s.name.startswith("s1"))
    assert step.input["records"] == ["Vendor", "WorkOrder"] and step.input["tables"] == ["vendors", "work_orders"]
