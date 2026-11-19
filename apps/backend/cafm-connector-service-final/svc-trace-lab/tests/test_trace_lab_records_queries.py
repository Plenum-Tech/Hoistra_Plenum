"""Which records queries a live question's turn gets (18 Nov 2026, Hussain and Bala): Hoistra's own, from the turn's trace;
else Trace Lab's fixed answer-key query; else one Hoistra's own planner proposes (only when the turn called a data
tool); else none, with the reason. No database and no model: nodes are built by hand in the shapes the tree view stores,
and the planner and the map are faked.

Where the shapes come from (no real tree could be read in the cloud):
  - a records-tool node: svc-deepagents trace.py on_activity (stage "tool": kind "tool", name = the tool, input =
    what the model passed, e.g. {"question": ...}, output = the ToolMessage content, a JSON string) and
    langfuse_export.filled_io / trace_tree._node (type, filled, seq, parent_id);
  - the planner's model call: trace.py on_model_start/on_model_end (an llm span whose output is
    {"content": "<the plan JSON>", "tool_calls": []}). Inside a tool its depth is above 2, so it hangs under the
    "sub-agent" group, not under the tool (test_trace.py: "a sub-agent's call"); the group's own output is filled from
    its last model call (filled.output_from);
  - what the records tool returns: ontology_qa.answer_question / answer_from_records (found: false, ok: false + error,
    or the evidence dict, which has no `found`)."""
from __future__ import annotations

import ast
import json
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.agents import ontology_qa as oq
from trace_lab import answer_key as ak
from trace_lab import records_queries as rq
from trace_lab import records_run, trace_tree


def _plan(concept, states=(), match=None):
    return {"question_type": "list",
            "focus": {"concept": concept, "match": match, "states": list(states), "filters": []},
            "constraints": [], "include": [], "aggregate": None}


PLAN_A = _plan("Decision", ["pending"])
PLAN_A_RETRY = _plan("Decision", ["owed"])
PLAN_B = _plan("InspectionReport", ["to_convert"])
PLAN_C = _plan("Vendor", ["blocked"])
Q_A = "decisions owed at Bishopsgate"
Q_B = "inspection reports to convert at Bishopsgate"

OUT_FOUND = json.dumps({"ok": True, "question": Q_A, "interpretation": "Decision records", "header": "Decision records: 3 match",
                        "records": "[]", "truncated": False, "total": 3, "paths": [], "answer_rules": "..."})
OUT_NOT_FOUND = json.dumps({"ok": True, "found": False, "question": Q_A, "answer_hint": "Could not find 'Bishopsgate' in the records",
                            "notes": [], "queries": []})
OUT_ERROR = json.dumps({"ok": False, "error": "Could not plan this question against the ontology: Could not build a valid plan"})
OUT_LOADING = json.dumps({"ok": False, "found": False, "still_loading": True, "answer_hint": "The records are still loading"})


def tool(id_, name, inp=None, *, parent=None, seq=None, out=OUT_FOUND, **kw):
    return {"id": id_, "parent_id": parent, "depth": 1, "seq": seq, "kind": "tool", "type": "tool", "name": name,
            "agent": "tool:unknown", "ok": True, "error": None, "level": "DEFAULT", "input": inp, "output": out,
            "filled": {}, "truncated": False, **kw}


def llm(id_, parent=None, plan=None, *, seq=None, **kw):
    return {"id": id_, "parent_id": parent, "depth": 2, "seq": seq, "kind": "llm", "type": "generation", "name": "model",
            "agent": "sub-agent", "model": "gpt-5.6-terra", "ok": True, "error": None, "level": "DEFAULT", "input": None,
            "output": {"content": json.dumps(plan) if plan is not None else "I cannot", "tool_calls": []},
            "filled": {}, "truncated": False, **kw}


def group(id_, name="sub-agent", *, seq=None, copy_of=None, **kw):
    """An agent span: it records nothing itself, so the tree view fills its output from its last step."""
    return {"id": id_, "parent_id": "root", "depth": 1, "seq": seq, "kind": "agent", "type": "agent", "name": name,
            "agent": name, "ok": True, "error": None, "level": "DEFAULT", "input": None,
            "output": {"content": json.dumps(copy_of), "tool_calls": []} if copy_of is not None else None,
            "filled": {"output_from": "its last step (model)"} if copy_of is not None else {}, "truncated": False, **kw}


# ── what a records tool call keeps: its last plan and its question ─────────────────────────────────────────────────

def test_each_records_tool_call_keeps_its_last_plan_and_its_question():
    nodes = [tool("t1", "answer_from_records", {"question": "decisions owed at Bishopsgate"}),
             llm("p1", parent="t1", plan=PLAN_A_RETRY), llm("p2", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": "inspection reports to convert at Bishopsgate"}),
             llm("p3", parent="t2", plan=PLAN_B)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": "decisions owed at Bishopsgate"},
                                         {"plan": PLAN_B, "question": "inspection reports to convert at Bishopsgate"}]


def test_the_tool_input_may_be_a_dict_a_json_string_or_cut():
    nodes = [tool("t1", "answer_from_records", json.dumps({"question": Q_A})), llm("p1", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"_truncated": '{"question": "inspection reports to conv'}),
             llm("p2", parent="t2", plan=PLAN_B),
             tool("t3", "answer_from_records", None), llm("p3", parent="t3", plan=PLAN_C),
             tool("t4", "answer_from_records", {"question": "   "}), llm("p4", parent="t4", plan=PLAN_A_RETRY)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}, {"plan": PLAN_B, "question": None},
                                         {"plan": PLAN_C, "question": None}, {"plan": PLAN_A_RETRY, "question": None}]


def test_a_plan_is_any_descendant_of_the_tool_not_only_its_children():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}), group("g1", seq=2),
             llm("p1", parent="g1", plan=PLAN_A, seq=3)]
    nodes[1]["parent_id"] = "t1"
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_only_tool_nodes_named_for_the_records_tool_count():
    nodes = [{**llm("m1", plan=PLAN_A), "name": "answer_from_records"},
             tool("t1", "list_building_certificates", {"question": Q_A}), llm("p1", parent="t1", plan=PLAN_B),
             {**tool("d1", "answer_from_records"), "type": "span", "kind": "db"}]
    assert rq.extract_queries(nodes) == []
    assert rq.extract_queries([]) == [] and rq.extract_queries(None) == []


def test_a_call_whose_plan_cannot_be_found_gives_no_query():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}), llm("p1", parent="t1", plan=None)]
    assert rq.extract_queries(nodes) == []


# ── the planner's calls are not under the tool in Hoistra's trace: they are placed by order ───────────────────────

def test_planner_calls_under_the_sub_agent_group_go_to_the_call_before_them():
    """trace.py puts a model run inside a tool (depth > 2) under one "sub-agent" group, so by seq: t1 p1 p2 | t2 p3.
    The tree lists the group's children together (depth first), so p3 is listed before t2 though it began after it."""
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, parent="root", seq=1),
             group("g1", seq=2, copy_of=PLAN_B),
             llm("p1", parent="g1", plan=PLAN_A_RETRY, seq=3), llm("p2", parent="g1", plan=PLAN_A, seq=4),
             llm("p3", parent="g1", plan=PLAN_B, seq=6),
             tool("t2", "answer_from_records", {"question": Q_B}, parent="root", seq=5)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}, {"plan": PLAN_B, "question": Q_B}]


def test_the_groups_filled_copy_of_a_plan_is_not_a_plan_of_its_own():
    # The first call's planner wrote no plan; the group's output is its LAST model call's, a copy of the second call's.
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, seq=1), group("g1", seq=2, copy_of=PLAN_B),
             llm("p1", parent="g1", plan=None, seq=3), llm("p2", parent="g1", plan=PLAN_B, seq=5),
             tool("t2", "answer_from_records", {"question": Q_B}, seq=4)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_B, "question": Q_B}]


def test_without_seq_the_order_of_the_nodes_is_used():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}), llm("p1", parent="g1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": Q_B}), llm("p2", parent="g1", plan=PLAN_B)]
    assert [q["plan"] for q in rq.extract_queries(nodes)] == [PLAN_A, PLAN_B]


def test_a_plan_under_the_tool_wins_over_one_that_is_only_nearby():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, seq=1), llm("near", parent="g1", plan=PLAN_C, seq=2),
             llm("own", parent="t1", plan=PLAN_A, seq=3)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_a_plan_below_another_records_call_is_not_borrowed_by_an_earlier_one():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, seq=1),
             tool("t2", "answer_from_records", {"question": Q_B}, seq=2), llm("p2", parent="t2", plan=PLAN_B, seq=3)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_B, "question": Q_B}]


def test_nodes_built_by_the_tree_view_from_hoistras_spans():
    """The real thing end to end: span rows as trace.py writes them, built into nodes by trace_tree.build_tree."""
    t0 = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

    def span(i, parent, seq, kind, name, **kw):
        return {"id": i, "turn_id": "turn-rq", "parent_id": parent, "seq": seq, "kind": kind, "name": name,
                "agent": kw.pop("agent", "orchestrator"), "started_at": t0 + timedelta(milliseconds=seq * 10),
                "ended_at": t0 + timedelta(milliseconds=seq * 10 + 5), "latency_ms": 5, **kw}

    turn = {"turn_id": "turn-rq", "session_id": "s", "organization_id": "0b1c2d3e-0000-4000-8000-000000000001",
            "user_id": "u", "email": "a@b.c", "question": Q_A + ", after that " + Q_B, "answer": "ok", "route": "single:x",
            "started_at": t0, "ended_at": t0 + timedelta(seconds=3), "latency_ms": 3000, "ok": True, "error": None}
    spans = [span("1", None, 0, "turn", "turn", input={"message": turn["question"]}, output={"answer": "ok"}),
             span("2", "1", 1, "tool", "answer_from_records", agent="tool:unknown", input={"question": Q_A}, output=OUT_FOUND),
             span("3", "2", 2, "db", "count", input={"sql": "SELECT count(*) FROM work_orders", "params": {}},
                  output={"row_count": 3}),
             span("4", "1", 3, "agent", "sub-agent", agent="sub-agent"),
             span("5", "4", 4, "llm", "model", agent="sub-agent", model="gpt-5.6-terra", input_tokens=100, output_tokens=50,
                  output={"content": json.dumps(PLAN_A_RETRY), "tool_calls": []}),
             span("6", "4", 5, "llm", "model", agent="sub-agent", model="gpt-5.6-terra", input_tokens=100, output_tokens=50,
                  output={"content": json.dumps(PLAN_A), "tool_calls": []}),
             span("7", "1", 6, "tool", "answer_from_records", agent="tool:unknown", input={"question": Q_B}, output=OUT_FOUND),
             span("8", "4", 7, "llm", "model", agent="sub-agent", model="gpt-5.6-terra", input_tokens=100, output_tokens=50,
                  output={"content": json.dumps(PLAN_B), "tool_calls": []})]
    nodes = trace_tree.build_tree(turn, spans)["nodes"]
    group_node = next(n for n in nodes if n["name"] == "sub-agent")
    assert group_node["filled"].get("output_from"), "the tree fills the group's output from its last step"
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}, {"plan": PLAN_B, "question": Q_B}]
    assert rq.data_tools(nodes) == []


# ── a retry replaces the call before it ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("first", [OUT_NOT_FOUND, OUT_ERROR, OUT_LOADING], ids=["not found", "error", "still loading"])
def test_a_call_that_found_nothing_or_failed_is_dropped_when_a_later_call_follows(first):
    nodes = [tool("t1", "answer_from_records", {"question": "decisions owed at Bishopsgat"}, out=first),
             llm("p1", parent="t1", plan=PLAN_A_RETRY),
             tool("t2", "answer_from_records", {"question": Q_A}), llm("p2", parent="t2", plan=PLAN_A)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_the_last_call_is_kept_even_when_it_found_nothing():
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}), llm("p1", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": Q_B}, out=OUT_NOT_FOUND), llm("p2", parent="t2", plan=PLAN_B)]
    assert [q["plan"] for q in rq.extract_queries(nodes)] == [PLAN_A, PLAN_B]


def test_a_chain_of_failed_calls_keeps_only_the_one_that_ended_it():
    nodes = [tool("t1", "answer_from_records", {"question": "a"}, out=OUT_NOT_FOUND), llm("p1", parent="t1", plan=PLAN_A_RETRY),
             tool("t2", "answer_from_records", {"question": "b"}, out=OUT_ERROR), llm("p2", parent="t2", plan=PLAN_C),
             tool("t3", "answer_from_records", {"question": Q_A}), llm("p3", parent="t3", plan=PLAN_A)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_a_call_the_tool_node_marks_as_failed_is_a_retry_too():
    failed = tool("t1", "answer_from_records", {"question": Q_A}, out=None, ok=False, error="timeout", level="ERROR")
    nodes = [failed, llm("p1", parent="t1", plan=PLAN_A_RETRY),
             tool("t2", "answer_from_records", {"question": Q_A}), llm("p2", parent="t2", plan=PLAN_A)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_a_failed_call_stays_when_the_call_after_it_made_no_plan():
    # The planner could not build one the second time: the first call's query is the only one there is.
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, out=OUT_NOT_FOUND), llm("p1", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": Q_A}, out=OUT_ERROR)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


def test_an_output_that_cannot_be_read_does_not_make_a_call_a_retry():
    # Hoist Traces cuts a long output; a cut JSON string is not parsed, and such a call found its records.
    cut = '{"ok": true, "question": "decisions", "records": "[{...' + "x" * 50 + "… [9000 more chars]"
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}, out=cut), llm("p1", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": Q_B}, out={"_truncated": '{"ok": true'}),
             llm("p2", parent="t2", plan=PLAN_B),
             tool("t3", "answer_from_records", {"question": "c"}, out=json.loads(OUT_FOUND)), llm("p3", parent="t3", plan=PLAN_C)]
    assert [q["plan"] for q in rq.extract_queries(nodes)] == [PLAN_A, PLAN_B, PLAN_C]


def test_an_output_that_is_a_dict_is_read_like_its_json():
    nodes = [tool("t1", "answer_from_records", {"question": "a"}, out=json.loads(OUT_NOT_FOUND)), llm("p1", parent="t1", plan=PLAN_A_RETRY),
             tool("t2", "answer_from_records", {"question": Q_A}), llm("p2", parent="t2", plan=PLAN_A)]
    assert rq.extract_queries(nodes) == [{"plan": PLAN_A, "question": Q_A}]


# ── the data tools a turn called ───────────────────────────────────────────────────────────────────────────────────

def _registered_tool_names():
    """Every tool Hoistra defines: the functions decorated with @tool in svc-deepagents/src (the model calls a tool by its
    function name), read from the source - the orchestrator's _TOOL_DOMAIN lists only some of them."""
    names = set()
    for path in sorted(Path(oq.__file__).parent.parent.rglob("*.py")):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)       # a docstring in Hoistra's source has a bad escape
            tree = ast.parse(path.read_text(encoding="utf-8"))
        names.update(n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and any(ast.unparse(d).split("(")[0] == "tool" for d in n.decorator_list))
    assert len(names) > 150, "no tools found where Hoistra keeps them: update this test"
    return sorted(names)


DATA_TOOLS_SEEN = ["list_building_certificates", "score_vendor_work_orders", "compute_site_eui", "get_maintenance_overview",
                   "query_docs", "list_vendor_scorecards", "get_inspection_intelligence", "list_work_orders",
                   "udr_read_records", "scan_energy_anomalies"]


def test_data_tools_are_the_tool_nodes_that_are_not_the_records_tool_task_final_answer_or_verify():
    nodes = [tool("t0", "task"), tool("t1", "answer_from_records"), tool("t2", "list_building_certificates"),
             tool("t3", "verify_vendor_invoice"), tool("t4", "verify_accreditation_now"), tool("t5", "final_answer"),
             tool("t6", "score_vendor_work_orders"), tool("t7", "list_building_certificates")]
    assert rq.data_tools(nodes) == ["list_building_certificates", "score_vendor_work_orders"], "each name once, in order"


def test_the_names_hoistras_data_tools_have_are_data_tools():
    assert rq.data_tools([tool(f"t{i}", n) for i, n in enumerate(DATA_TOOLS_SEEN)]) == DATA_TOOLS_SEEN


def test_only_tool_nodes_are_data_tools():
    nodes = [llm("m1", plan=PLAN_A), group("g1", "wo_engine"), {**tool("d1", "count"), "type": "span", "kind": "db"},
             {"id": "r", "parent_id": None, "kind": "turn", "type": "agent", "name": "chat"}]
    assert rq.data_tools(nodes) == [] and rq.data_tools([]) == [] and rq.data_tools(None) == []


def test_a_tool_node_may_say_it_is_a_tool_by_kind_or_by_type_alone():
    assert rq.data_tools([{"name": "compute_site_eui", "kind": "tool"}, {"name": "get_ratings_position", "type": "tool"}]) \
        == ["compute_site_eui", "get_ratings_position"]


#: Every tool Hoistra defines that READS the company's records or an engine's computed data, as reviewed on 19 Nov 2026
#: (controller ruling). A tool Hoistra adds appears in neither this list nor NOT_DATA_TOOLS until someone decides.
REVIEWED_DATA_TOOLS = [
    'answer_with_graph_context', 'check_requirements', 'check_vendor_registration_compliance',
    'compute_building_rating', 'compute_site_eui', 'consumption_by_asset',
    'count_compliance_certificates', 'deduce_asset_condition', 'find_asset',
    'find_location', 'find_ppm_schedules', 'generate_compliance_report',
    'generate_monthly_energy_report', 'generate_vendor_scorecard', 'get_anomaly_rollup',
    'get_approval_chain', 'get_asset_condition_rules', 'get_asset_condition_summary',
    'get_asset_details', 'get_asset_documents', 'get_asset_intelligence',
    'get_building_cost_drivers', 'get_compliance_coverage', 'get_compliance_saved_space_summary',
    'get_cost_savings', 'get_dashboard_stats', 'get_document_metadata',
    'get_energy_saved_space_summary', 'get_energy_tariffs', 'get_hoist_score',
    'get_inspection_intelligence', 'get_maintenance_overview', 'get_market_profiles',
    'get_mees_summary', 'get_operating_hours', 'get_pack_facts',
    'get_ppm_contracts', 'get_ratings_position', 'get_score_weights',
    'get_statutory_duties', 'get_vendor_passport', 'get_work_order',
    'get_work_order_history', 'get_work_order_status_track', 'investigate_asset',
    'list_asset_conditions', 'list_blocked_vendors', 'list_building_certificates',
    'list_building_documents', 'list_building_meter_readings', 'list_compliance_approvals',
    'list_contract_approvals', 'list_contract_parameters', 'list_country_pack',
    'list_energy_anomalies', 'list_energy_approvals', 'list_energy_buildings',
    'list_invoices', 'list_maintenance_decisions', 'list_regulatory_filings',
    'list_tm46_benchmarks', 'list_unscored_assets', 'list_vendor_accreditations',
    'list_vendor_scorecards', 'list_vendors_by_certificate_count', 'list_verification_registers',
    'list_work_orders', 'lookup_user', 'query_docs',
    'query_table', 'replacement_candidates', 'resolve_cross_source_links',
    'retrieve_vector_evidence', 'run_compliance_scan', 'scan_chiller_efficiency',
    'scan_energy_anomalies', 'score_vendor_work_orders', 'score_work_orders_from_udr',
    'search_assets', 'search_documents', 'search_locations',
    'semantic_search', 'summarise_anomalies', 'udr_agent_query',
    'udr_execute_select', 'udr_get_record', 'udr_read_records',
    'udr_search_records', 'vendor_work_orders_at_building', 'work_order_blockers',
]


def test_every_tool_hoistra_registers_is_classified():
    names = _registered_tool_names()
    got = rq.data_tools([tool(f"t{i}", n) for i, n in enumerate(names)])
    assert got == REVIEWED_DATA_TOOLS, (
        "Hoistra's tools changed. New: %s. Gone: %s. Decide for each new tool whether it READS the company's records or an "
        "engine's data (add it to REVIEWED_DATA_TOOLS) or is a meta / write / action tool (add it to NOT_DATA_TOOLS)."
        % (sorted(set(got) - set(REVIEWED_DATA_TOOLS)), sorted(set(REVIEWED_DATA_TOOLS) - set(got))))


def test_the_tools_listed_as_not_data_are_tools_hoistra_has():
    names = set(_registered_tool_names())
    assert "task" in names and any(n.startswith("verify") for n in names), "the names the rule leaves out still exist"
    gone = rq.NOT_DATA_TOOLS - names - {"final_answer"}       # final_answer is a stage in Hoistra's code, not a tool
    assert not gone, "listed in NOT_DATA_TOOLS but not a tool of Hoistra's any more: " + ", ".join(sorted(gone))
    assert oq.answer_from_records.name == rq.RECORDS_TOOL == "answer_from_records", "the records tool is still named so"
    assert set(REVIEWED_DATA_TOOLS).isdisjoint(rq.NOT_DATA_TOOLS) and set(REVIEWED_DATA_TOOLS) <= names


# ── where a turn's queries come from ───────────────────────────────────────────────────────────────────────────────

NODES_WITH_PLAN = [tool("t1", "answer_from_records", {"question": Q_A}), llm("p1", parent="t1", plan=PLAN_A)]
NODES_WITH_DATA_TOOL = [tool("t9", "score_vendor_work_orders", {"vendor": "Apex"}, out="{}")]


def _planner(monkeypatch, *, plan=None, raise_=None, map_raises=None, calls=None):
    """Fake Hoistra's planner and map: plan_question returns (plan, attempts) as the real one does."""
    async def load_map(*a, **k):
        if map_raises is not None:
            raise map_raises
        return "the-map"

    async def plan_question(onto, question, *a, **k):
        if calls is not None:
            calls.append((onto, question))
        if raise_ is not None:
            raise raise_
        return plan, [{"attempt": 1, "errors": []}]

    monkeypatch.setattr(records_run, "load_map", load_map)
    monkeypatch.setattr(oq, "plan_question", plan_question)


async def test_order_of_sources(monkeypatch):
    # Hoistra's plans win; else the answer key; else a proposal; else none
    _planner(monkeypatch, raise_=AssertionError("the planner must not be called when a query is already known"))
    got = await rq.queries_for(NODES_WITH_PLAN, "x", propose=True)
    assert got == {"queries": [{"source": "hoistra", "plan": PLAN_A, "question": Q_A}], "none": None}
    got = await rq.queries_for(NODES_WITH_DATA_TOOL, "How many vendors are blocked right now?", propose=True)
    key, params = ak.match("How many vendors are blocked right now?")
    assert got == {"queries": [{"source": "answer_key", "plan": key.plan(params),
                                "question": "How many vendors are blocked right now?"}], "none": None}
    _planner(monkeypatch, plan=PLAN_A)
    got = await rq.queries_for(NODES_WITH_DATA_TOOL, "What is the vendor score for Apex?", propose=True)
    assert got["queries"] == [{"source": "proposed", "plan": PLAN_A, "question": "What is the vendor score for Apex?"}]
    assert got["none"] is None
    assert (await rq.queries_for(NODES_WITH_DATA_TOOL, "What is the vendor score for Apex?", propose=False)) == {"queries": [], "none": None}


async def test_hoistras_query_takes_the_turns_question_when_the_call_kept_none(monkeypatch):
    _planner(monkeypatch, raise_=AssertionError("not called"))
    nodes = [tool("t1", "answer_from_records", None), llm("p1", parent="t1", plan=PLAN_A)]
    got = await rq.queries_for(nodes, "decisions owed", propose=False)
    assert got["queries"] == [{"source": "hoistra", "plan": PLAN_A, "question": "decisions owed"}]


async def test_several_calls_are_several_hoistra_queries(monkeypatch):
    nodes = [tool("t1", "answer_from_records", {"question": Q_A}), llm("p1", parent="t1", plan=PLAN_A),
             tool("t2", "answer_from_records", {"question": Q_B}), llm("p2", parent="t2", plan=PLAN_B)]
    got = await rq.queries_for(nodes, "both", propose=False)
    assert got == {"queries": [{"source": "hoistra", "plan": PLAN_A, "question": Q_A},
                               {"source": "hoistra", "plan": PLAN_B, "question": Q_B}], "none": None}


async def test_the_planner_is_asked_the_question_with_hoistras_map(monkeypatch):
    calls = []
    _planner(monkeypatch, plan=PLAN_A, calls=calls)
    await rq.queries_for(NODES_WITH_DATA_TOOL, "What is the vendor score for Apex?", propose=True)
    assert calls == [("the-map", "What is the vendor score for Apex?")]


async def test_the_proposed_plan_is_the_planners_plan_not_its_attempts(monkeypatch):
    async def load_map():
        return "the-map"

    async def plan_question(onto, question, *a, **k):
        return PLAN_C, [{"attempt": 1, "errors": ["x"]}, {"attempt": 2, "errors": []}]
    monkeypatch.setattr(records_run, "load_map", load_map)
    monkeypatch.setattr(oq, "plan_question", plan_question)
    got = await rq.queries_for(NODES_WITH_DATA_TOOL, "q", propose=True)
    assert got["queries"][0]["plan"] == PLAN_C


async def test_a_planner_that_fails_gives_no_query_and_says_why(monkeypatch):
    _planner(monkeypatch, raise_=oq.OntologyError("Could not build a valid plan"))
    assert await rq.queries_for(NODES_WITH_DATA_TOOL, "hello", propose=True) == {"queries": [], "none": "not_about_records"}
    _planner(monkeypatch, raise_=RuntimeError("401 no key"))
    assert (await rq.queries_for(NODES_WITH_DATA_TOOL, "x", propose=True))["none"] == "planner_unavailable"
    _planner(monkeypatch, plan=PLAN_A, map_raises=records_run.NotReady("not built"))
    assert await rq.queries_for(NODES_WITH_DATA_TOOL, "x", propose=True) == {"queries": [], "none": "no_map"}
    _planner(monkeypatch, plan=PLAN_A, map_raises=RuntimeError("disk"))
    assert (await rq.queries_for(NODES_WITH_DATA_TOOL, "x", propose=True))["none"] == "planner_unavailable"
    _planner(monkeypatch, plan=PLAN_A, map_raises=oq.OntologyError("map"))
    assert (await rq.queries_for(NODES_WITH_DATA_TOOL, "x", propose=True))["none"] == "planner_unavailable", \
        "an error in the map is not the question's"


async def test_no_data_tool_means_no_proposal_and_the_planner_is_not_asked(monkeypatch):
    calls = []
    _planner(monkeypatch, plan=PLAN_A, calls=calls)
    for nodes in ([], [tool("t1", "answer_from_records", {"question": "hello"})],
                  [tool("t2", "task"), tool("t3", "verify_vendor_invoice"), tool("t4", "final_answer")]):
        assert await rq.queries_for(nodes, "hello", propose=True) == {"queries": [], "none": "not_about_records"}
    assert calls == []


# Meta tools and write/action tools never read the company's records or an engine's data, so they never make a turn
# eligible for a proposed query (controller ruling, 19 Nov 2026).
META_AND_WRITE_TOOLS = ["write_todos", "select_skill", "memory_get", "memory_set", "read_file", "write_file", "compact_context",
                        "create_work_order", "update_work_order", "close_work_order", "create_intelligent_work_order",
                        "prepare_intelligent_work_order", "send_approval_request_email", "request_approval_chain",
                        "respond_to_approval_step", "udr_create_record", "udr_update_record", "udr_delete_record",
                        "upsert_compliance_certificate", "set_remedial_status", "decide_compliance_approval",
                        "draft_certificate_renewal", "decide_contract_approval", "approve_asset_criticality",
                        "update_score_weights", "upsert_energy_meter", "ingest_meter_readings", "act_on_energy_anomaly",
                        "decide_energy_approval", "index_document", "delete_document", "start_migration", "run_migration",
                        "submit_field_mapping", "configure_fiix_credentials", "share_vendor_passport"]


@pytest.mark.parametrize("name", META_AND_WRITE_TOOLS)
def test_a_meta_or_write_tool_is_not_a_data_tool(name):
    assert rq.data_tools([tool("t1", name)]) == []


async def test_a_turn_whose_only_tools_are_meta_or_write_tools_gets_no_proposal(monkeypatch):
    calls = []
    _planner(monkeypatch, plan=PLAN_A, calls=calls)
    nodes = [tool("t1", "write_todos"), tool("t2", "select_skill"), tool("t3", "memory_set"), tool("t4", "task"),
             tool("t5", "create_work_order", {"title": "Leak"}), tool("t6", "send_approval_request_email"),
             tool("t7", "answer_from_records", {"question": "hello"}, out=OUT_NOT_FOUND)]
    assert await rq.queries_for(nodes, "please raise a work order for the leak", propose=True) == \
        {"queries": [], "none": "not_about_records"}
    assert calls == [], "the planner is not asked"


async def test_a_read_tool_beside_meta_tools_still_gets_a_proposal(monkeypatch):
    calls = []
    _planner(monkeypatch, plan=PLAN_C, calls=calls)
    nodes = [tool("t1", "write_todos"), tool("t2", "select_skill"), tool("t3", "list_building_certificates"),
             tool("t4", "memory_set")]
    assert rq.data_tools(nodes) == ["list_building_certificates"]
    got = await rq.queries_for(nodes, "which certificates are about to lapse", propose=True)
    assert got == {"queries": [{"source": "proposed", "plan": PLAN_C, "question": "which certificates are about to lapse"}],
                   "none": None}
    assert calls == [("the-map", "which certificates are about to lapse")]


async def test_a_records_call_without_a_plan_still_falls_through_to_the_key_and_the_proposal(monkeypatch):
    _planner(monkeypatch, plan=PLAN_C)
    nodes = [tool("t1", "answer_from_records", {"question": "x"}, out=OUT_ERROR), tool("t2", "list_vendor_scorecards")]
    got = await rq.queries_for(nodes, "How many vendors are blocked right now?", propose=True)
    assert got["queries"][0]["source"] == "answer_key"
    got = await rq.queries_for(nodes, "something else entirely", propose=True)
    assert got["queries"][0]["source"] == "proposed"


async def test_without_a_proposal_nothing_found_is_none_with_no_reason(monkeypatch):
    _planner(monkeypatch, raise_=AssertionError("not called"))
    assert await rq.queries_for([], "hello", propose=False) == {"queries": [], "none": None}


def test_the_words_for_each_source():
    assert rq.SOURCE_WORDS == {"hoistra": "Hoistra’s query", "answer_key": "Trace Lab’s fixed query",
                               "proposed": "Proposed for this question - check it before accepting"}
