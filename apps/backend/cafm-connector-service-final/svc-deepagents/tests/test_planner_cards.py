"""A planned answer reaches the chat as cards built from the step results, not from the prose."""
from __future__ import annotations

from src.agents import planner

DECISIONS = {"ok": True, "count": 3, "total": 3, "by_state": {"Blocked": 1, "Awaiting approval": 2}, "decisions": [
    {"work_order": "WO-B-301-4562", "state": "Blocked", "asset": "Fire alarm panel", "vendor": "Pennard Fire Services",
     "detail": "Blocked - BAFE lapsed", "statutory": True,
     "statutory_certificate": {"certificate_type": "FIRE_ALARM_SERVICE", "expires": "2026-10-28", "lapsed": False}},
    {"work_order": "WO-B-301-4527", "state": "Awaiting approval", "asset": "AHU-3", "trigger": "Approval outstanding", "statutory": False, "statutory_certificate": None},
    {"work_order": "WO-B-301-4533", "state": "Awaiting approval", "asset": "Lift Asset-4471", "trigger": "LOLER before expiry", "statutory": True,
     "statutory_certificate": {"certificate_type": "LOLER", "expires": "2026-10-05", "lapsed": False}}]}
SAVINGS = {"ok": True, "open_jobs": [{"wo_code": "WO-B-301-4527", "what": "AHU-3 bearing", "cost_to_act": 1800, "energy_waste_gbp_per_year": 40151.0},
                                     {"wo_code": "WO-B-301-4565", "what": "Boiler 1 burner", "cost_to_act": 420, "energy_waste_gbp_per_year": 8202.0}],
           "repeat_failures": [{"asset": "LIFT", "cost_to_replacement_ratio": 1.26}, {"asset": "PUMP", "cost_to_replacement_ratio": 0.1}]}
PLAN = {"goal": "Decisions, statutory, cost of delay", "steps": [
    {"id": "s1", "kind": "tool", "target": "list_maintenance_decisions", "ask": "decisions owed", "depends_on": [], "why": ""},
    {"id": "s2", "kind": "tool", "target": "get_cost_savings", "ask": "cost", "depends_on": [], "why": ""}]}
RESULTS = {"s1": {"ok": True, "output": DECISIONS, "tool_calls": [], "ms": 900}, "s2": {"ok": True, "output": SAVINGS, "tool_calls": [], "ms": 1200}}


def test_decisions_and_savings_become_kpis_groups_and_statutory_first_actions():
    resp, pipe = planner.cards_from_results("What needs my decision today?", PLAN, RESULTS,
                                            "**3 decisions** are owed today; 2 are statutory.\n\n| a | b |", cost={"usd": 0.05})
    labels = {k["label"]: k for k in resp["kpis"]}
    assert labels["Decisions owed"]["count"] == 3 and labels["Blocked"]["count"] == 1
    assert labels["Statutory"]["count"] == 2 and labels["Statutory"]["severity"] == "critical"
    assert labels["£/yr at stake"]["count"] == 48353 and labels["Replace or fix"]["count"] == 1
    owners = [g["owner"] for g in resp["groups"]]
    assert owners[:2] == ["Blocked", "Awaiting approval"] and "Open jobs with money at stake" in owners
    blocked = resp["groups"][0]
    assert blocked["severity"] == "critical" and "statutory: FIRE_ALARM_SERVICE expires 2026-10-28" in blocked["points"][0]
    # statutory actions first (soonest expiry), then the cost-saving approvals; every action has the renderer's fields
    assert resp["actions"][0]["title"].startswith("Approve WO-B-301-4533 — Lift Asset-4471: LOLER expires 2026-10-05")
    assert resp["actions"][1]["title"].startswith("Unblock WO-B-301-4562")
    assert all(set(a) >= {"title", "scope", "severity", "tags", "cert_ids", "sub_question_id"} for a in resp["actions"])
    assert resp["narrative"] == "3 decisions are owed today; 2 are statutory."
    assert [s["stage"] for s in pipe["steps"]] == ["plan", "data", "data", "validate"] and pipe["cost"] == {"usd": 0.05}
    assert "3 rows · 900 ms" in pipe["steps"][1]["detail"]


def test_a_turn_with_no_recognised_result_gets_a_pipeline_but_no_cards():
    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": "x", "depends_on": [], "why": ""}]}
    resp, pipe = planner.cards_from_results("q", plan, {"s1": {"ok": True, "output": "prose answer", "tool_calls": [], "ms": 5}}, "Some prose.")
    assert resp["kpis"] == [] and resp["groups"] == [] and resp["narrative"] == ""
    assert len(pipe["steps"]) == 3 and pipe["engine"] == "orchestrator"


def test_replacement_and_blocker_results_build_their_own_cards():
    repl = {"ok": True, "eol_grade": 4, "summary": {"parts_to_reorder": 2, "stock_outs": 0, "end_of_life": 2, "replace_or_fix": 15, "purchase_orders_open": 0},
            "parts_to_reorder": [{"part": "AHU filter set", "stock": 1, "reorder_level": 4, "cost_to_restock": 495.0, "supplier": "Apex"}],
            "end_of_life": [{"asset": "B-301-CHILLER-101", "condition_grade": 4, "replacement_value": 140000.0, "design_life_used_pct": 87.8}]}
    blk = {"ok": True, "summary": {"blocked": 1, "at_risk": 0, "clear": 3},
           "blocked": [{"wo_code": "WO-1", "title": "Loop 2", "vendor": "Pennard", "blockers": [{"detail": "BAFE lapsed on 2026-09-14"}]}], "at_risk": []}
    plan = {"steps": [{"id": "s1", "kind": "tool", "target": "replacement_candidates", "ask": "", "depends_on": [], "why": ""},
                      {"id": "s2", "kind": "tool", "target": "work_order_blockers", "ask": "", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {"s1": {"ok": True, "output": repl, "tool_calls": []}, "s2": {"ok": True, "output": blk, "tool_calls": []}}, "x")
    labels = {k["label"]: k["count"] for k in resp["kpis"]}
    assert labels["End of life"] == 2 and labels["Parts to reorder"] == 2 and labels["Blocked by vendor"] == 1
    assert any("£140,000" in p for g in resp["groups"] for p in g["points"])
    assert resp["actions"][0]["severity"] == "critical"



def test_any_turn_with_a_recognised_tool_call_gets_cards_and_one_with_its_own_does_not():
    calls = [{"tool": "select_skill", "input": {}, "output": "{}"},
             {"tool": "replacement_candidates", "input": {"building_name": "Bishopsgate Tower"},
              "output": {"ok": True, "eol_grade": 4, "summary": {"parts_to_reorder": 2, "stock_outs": 0, "end_of_life": 2, "replace_or_fix": 15, "purchase_orders_open": 0},
                         "parts_to_reorder": [], "end_of_life": [{"asset": "B-301-CHILLER-101", "condition_grade": 4, "replacement_value": 140000.0}]}}]
    built = planner.cards_from_tool_calls("which assets to replace?", calls, "Two assets are at grade 4.\n\nmore")
    assert built is not None
    resp, pipe = built
    assert {k["label"]: k["count"] for k in resp["kpis"]}["End of life"] == 2 and resp["narrative"] == "Two assets are at grade 4."
    assert [s["label"] for s in pipe["steps"]][:2] == ["select_skill", "replacement_candidates"]
    # JSON-string outputs are read too
    calls2 = [{"tool": "work_order_blockers", "input": {}, "output": '{"ok": true, "summary": {"blocked": 1, "at_risk": 0}, "blocked": [{"wo_code": "WO-1", "title": "x", "vendor": "V", "blockers": [{"detail": "BAFE lapsed"}]}], "at_risk": []}'}]
    assert planner.cards_from_tool_calls("", calls2, "x")[0]["kpis"][0]["count"] == 1
    # a turn that already carries cards, or made no recognised call, is left alone
    assert planner.cards_from_tool_calls("", [{"tool": "compliance_response", "output": {}}, *calls], "x") is None
    assert planner.cards_from_tool_calls("", [{"tool": "task", "output": "prose"}], "x") is None


def test_attach_route_to_result_adds_the_cards_to_the_turn():
    from src.agents.session_workspace import attach_route_to_result
    calls = [{"tool": "work_order_blockers", "input": {}, "output": {"ok": True, "summary": {"blocked": 2, "at_risk": 1}, "blocked": [], "at_risk": []}}]
    out = attach_route_to_result({"session_id": "s-cards", "answer": "2 blocked.", "tool_calls": calls}, "s-cards", intent="general", domain="wo_engine")
    names = [t["tool"] for t in out["tool_calls"]]
    assert names == ["work_order_blockers", "compliance_pipeline", "compliance_response"]
    assert out["tool_calls"][-1]["output"]["kpis"][0] == {"count": 2, "label": "Blocked by vendor", "sublabel": "cannot proceed today", "severity": "critical", "unit": "other", "cert_ids": []}


# Seen 5 Oct 2026: "Show me everything that's at risk" was answered by a plan step on
# get_asset_condition_summary; no builder knew that tool, so the answer reached the chat as a
# markdown table and seven numbered lists while every other answer rendered as cards.
CONDITION = {"ok": True,
             "summary": {"assets": 61, "threat": 6, "watch": 9, "in_control": 46, "watch_shares_section": 7,
                         "watch_persistent_anomaly": 2, "in_control_anomaly_under_threshold": 3, "section_not_measured": 12},
             "buildings": [{"building": "Bishopsgate Tower", "threat": 6, "watch": 9, "in_control": 46, "assets": 61,
                            "eui_kwh_per_m2": 214.82, "reference_eui_kwh_m2": 212.0, "deviation_pct": 1.3,
                            "work_orders_open": 13, "inspections_recommended": 140, "sections": 4, "sections_over_reference": 2}],
             "sections": [{"section": "Central Plant · Basement", "building": "Bishopsgate Tower", "eui_kwh_per_m2": 262.0,
                           "reference_eui_kwh_m2": 180.0, "deviation_pct": 45.6, "over_reference": True, "threat": 5, "watch": 5, "in_control": 0, "assets": 10},
                          {"section": "L4 East · Tenant Floor", "building": "Bishopsgate Tower", "eui_kwh_per_m2": 234.8,
                           "reference_eui_kwh_m2": 180.0, "deviation_pct": 30.4, "over_reference": True, "threat": 1, "watch": 1, "in_control": 0, "assets": 2},
                          {"section": "L2 West", "building": "Bishopsgate Tower", "eui_kwh_per_m2": 150.0,
                           "reference_eui_kwh_m2": 180.0, "deviation_pct": -16.7, "over_reference": False, "threat": 0, "watch": 3, "in_control": 46, "assets": 49}]}


def test_the_asset_condition_summary_becomes_threat_watch_and_in_control_cards():
    plan = {"steps": [{"id": "s1", "kind": "tool", "target": "get_asset_condition_summary", "ask": "at risk", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("Show me everything that's at risk", plan,
                                         {"s1": {"ok": True, "output": CONDITION, "tool_calls": [], "ms": 40}},
                                         "Six assets are a threat, all at Bishopsgate Tower.\n\n| a |")
    k = {x["label"]: x for x in resp["kpis"]}
    assert k["Threat"]["count"] == 6 and k["Threat"]["severity"] == "critical"
    assert k["Watch"]["count"] == 9 and k["In control"]["count"] == 46
    assert "3 with an anomaly under threshold" in k["In control"]["sublabel"]
    over = next(g for g in resp["groups"] if g["owner"] == "Sections over reference")
    assert over["severity"] == "critical" and len(over["points"]) == 2      # the section under reference is not "over"
    assert over["points"][0].startswith("Central Plant · Basement, Bishopsgate Tower — 262 vs 180 kWh/m², +45.6%")
    assert any(g["owner"] == "By building" for g in resp["groups"])
    assert resp["actions"][0]["title"].startswith("Investigate Central Plant · Basement at Bishopsgate Tower")
    assert resp["narrative"] == "Six assets are a threat, all at Bishopsgate Tower."
    # the tool-call route gets the same cards
    built = planner.cards_from_tool_calls("q", [{"tool": "get_asset_condition_summary", "output": CONDITION}], "Six assets are a threat today.")
    assert built and {x["label"] for x in built[0]["kpis"]} >= {"Threat", "Watch", "In control"}


def test_an_engine_step_gets_cards_from_the_data_tools_it_called():
    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "energy", "ask": "at risk", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {"s1": {"ok": True, "output": "prose", "ms": 9,
                                                            "tool_calls": [{"tool": "get_asset_condition_summary", "output": CONDITION}]}},
                                         "Six assets are a threat, all at Bishopsgate Tower.")
    assert {x["label"] for x in resp["kpis"]} >= {"Threat", "Watch", "In control"}


def test_an_engine_step_with_its_own_dashboard_is_not_carded_twice():
    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": "x", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {"s1": {"ok": True, "output": "prose", "ms": 9, "tool_calls": [
        {"tool": "work_order_blockers", "output": {"ok": True, "summary": {"blocked": 1, "at_risk": 0}, "blocked": [], "at_risk": []}},
        {"tool": "compliance_response", "output": {"narrative": "own", "kpis": [{"count": 1}]}}]}}, "Some prose here today.")
    assert resp["kpis"] == []


def test_the_condition_tiles_are_counted_in_assets():
    plan = {"steps": [{"id": "s1", "kind": "tool", "target": "get_asset_condition_summary", "ask": "", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {"s1": {"ok": True, "output": CONDITION, "tool_calls": []}}, "x")
    assert {k["unit"] for k in resp["kpis"]} == {"assets"}



REPL = {"ok": True, "eol_grade": 4, "summary": {"parts_to_reorder": 3, "stock_outs": 0, "end_of_life": 0, "replace_or_fix": 0, "purchase_orders_open": 0},
        "parts_to_reorder": [], "end_of_life": []}


def test_one_tool_with_different_results_across_steps_is_not_carded_as_a_total():
    # "compare B-101 and B-102": each engine step read replacement_candidates for its building.
    other = dict(REPL, summary=dict(REPL["summary"], parts_to_reorder=7))
    plan = {"steps": [{"id": "s1", "kind": "engine", "target": "wo_engine", "ask": "B-101", "depends_on": [], "why": ""},
                      {"id": "s2", "kind": "engine", "target": "wo_engine", "ask": "B-102", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {
        "s1": {"ok": True, "output": "p", "tool_calls": [{"tool": "replacement_candidates", "output": REPL}]},
        "s2": {"ok": True, "output": "p", "tool_calls": [{"tool": "replacement_candidates", "output": other}]}}, "Some prose here.")
    assert not any(k["label"] == "Parts to reorder" for k in resp["kpis"])


def test_the_same_result_read_by_a_tool_step_and_an_engine_step_is_carded_once():
    plan = {"steps": [{"id": "s1", "kind": "tool", "target": "replacement_candidates", "ask": "", "depends_on": [], "why": ""},
                      {"id": "s2", "kind": "engine", "target": "wo_engine", "ask": "", "depends_on": [], "why": ""}]}
    resp, _ = planner.cards_from_results("q", plan, {
        "s1": {"ok": True, "output": REPL, "tool_calls": []},
        "s2": {"ok": True, "output": "p", "tool_calls": [{"tool": "replacement_candidates", "output": REPL}]}}, "x")
    assert [k["label"] for k in resp["kpis"]].count("Parts to reorder") == 1
