"""The planner's catalogue holds read tools only, each saying what it is not for; and a step
with dependents is checked before they build on it."""
from __future__ import annotations

import asyncio

from src.agents import planner


def test_write_tools_are_not_in_the_catalogue_and_read_tools_say_what_they_are_not_for():
    assert planner.catalogue_line("upsert_compliance_certificate", "Upsert a certificate.") is None
    assert planner.catalogue_line("decide_energy_approval", "Decide.") is None
    assert planner.catalogue_line("verify_vendor_invoice", "Verify.") is None
    line = planner.catalogue_line("list_asset_conditions", "E — Assets by ENERGY band.\nmore")
    assert line.startswith("E — Assets by ENERGY band.") and "Not for: the 1-5 condition grade" in line
    assert planner.catalogue_line("some_new_read_tool", "Reads things.") == "Reads things."


def test_the_live_catalogue_has_no_write_tool_and_names_the_near_misses():
    from src.agents.orchestrator import DeepAgentOrchestrator as O
    tools = O._planner_tools(O.__new__(O))
    assert not any(planner.WRITE_TOOL_RE.search(n) for n in tools), [n for n in tools if planner.WRITE_TOOL_RE.search(n)]
    assert "answer_from_records" in tools and "replacement_candidates" in tools and "work_order_blockers" in tools
    assert "Not for:" in tools["list_asset_conditions"] and "Not for:" in tools["search_documents"]


def test_parse_check_passes_when_it_cannot_say_why():
    assert planner.parse_check('{"answers_ask": false, "why": "every asset, not grade 4"}') == (False, "every asset, not grade 4")
    assert planner.parse_check('{"answers_ask": true, "why": "ok"}') == (True, "ok")
    assert planner.parse_check("garbage") == (True, "")
    p = planner.check_prompt({"ask": "assets with condition grade 4"}, {"count": 61, "total": 61, "assets": [{"asset_code": "A"}]})
    assert "assets with condition grade 4" in p and "1 rows:" in p


def test_a_failed_check_on_a_step_with_dependents_replans_and_a_leaf_step_is_not_checked():
    checked, replans = [], []
    OUT = {"ok": True, "records": [{"asset_code": "B-301-X", "status": "Open"}]}

    async def run_tool(name, args):
        return OUT

    async def run_engine(engine, ask, on_ev):
        return "", []

    async def check(step, result):
        checked.append(step["id"])
        return (False, "lists every asset, the ask said grade 4") if step["id"] == "s1" else (True, "")

    async def replan(question, plan, findings, results):
        replans.append(findings)
        return {"mode": "multi", "steps": [{"id": "s1", "kind": "tool", "target": "answer_from_records", "ask": "grade 4", "args": {"question": "g4"}, "depends_on": [], "why": ""}]}

    plan = {"mode": "multi", "steps": [
        {"id": "s1", "kind": "tool", "target": "list_work_orders", "ask": "list all assets", "args": {}, "depends_on": [], "why": ""},
        {"id": "s2", "kind": "tool", "target": "answer_from_records", "ask": "cost of those", "args": {"question": "x"}, "depends_on": ["s1"], "why": ""}]}
    results = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool, question="q", replan=replan, check=check))
    assert checked == ["s1"]                                  # s2 would be a leaf; the replanned rs1 is a leaf too
    assert replans and replans[0]["s1"][0]["kind"] == "check_failed"
    assert "rs1" in results and "s2" not in results
