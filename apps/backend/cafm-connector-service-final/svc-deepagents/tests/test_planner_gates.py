"""Step gates and the claim check: the two feedback channels that would have caught
turn-1ab9f7050c4a40d3 (61 of 61 "grade-4" assets; replacement value called unavailable)."""
from __future__ import annotations

import asyncio

from src.agents import planner

BANDED = {"ok": True, "count": 61, "total": 61, "method": "rule:x",
          "assets": [{"asset_code": f"B-301-{i}", "asset_name": f"A{i}", "band": "threat", "anomaly_annual_cost": 1.0} for i in range(61)]}
RECORDS = {"ok": True, "question": "q", "records": [{"asset_code": "B-301-CHILLER-101", "condition_score": 4, "replacement_value": 140000.0},
                                                    {"asset_code": "B-301-FCU-L20", "condition_score": 4, "replacement_value": 38000.0}]}


def test_a_filter_that_filtered_nothing_and_a_missing_field_are_found():
    step = {"id": "s1", "target": "list_asset_conditions", "ask": "List every asset with condition grade 4 and its replacement value"}
    kinds = [f["kind"] for f in planner.inspect_step(step, {"ok": True, "output": BANDED})]
    assert kinds == ["filter_did_nothing", "field_missing", "field_missing"]   # grade, replacement value
    # the records step that holds the fields is clean
    assert planner.inspect_step({"id": "s2", "ask": "replacement value of the grade 4 assets"}, {"ok": True, "output": RECORDS}) == []
    # a truncated result is flagged; a failed step is not re-flagged; a list question with no qualifier is not
    assert planner.inspect_step({"id": "s", "ask": "x"}, {"ok": True, "output": {"records": "[]", "truncated": True}})[0]["kind"] == "truncated"
    assert planner.inspect_step({"id": "s", "ask": "grade 4"}, {"ok": False, "output": None}) == []
    assert planner.inspect_step({"id": "s", "ask": "list all assets"}, {"ok": True, "output": BANDED}) == []


def test_the_claim_check_catches_unavailable_and_the_unfiltered_count():
    results = {"s1": {"ok": True, "output": BANDED}, "s2": {"ok": True, "output": RECORDS}}
    q = "Which assets are grade 4 and what would it cost to replace them?"
    answer = "The records identify 61 grade-4 assets. No replacement-cost records were returned, so the cost is unavailable."
    found = planner.verify_claims(answer, q, results)
    assert any("'replacement' unavailable, but step s2 returned 2 rows with replacement_value populated" in f for f in found)
    assert any("quotes 61 as the narrowed count" in f for f in found)
    assert planner.verify_claims("CHILLER-101 £140,000 and FCU-L20 £38,000: £178,000 in total.", q, results) == []


def test_execute_replans_once_with_the_findings_and_keeps_the_first_results():
    calls = []

    async def run_tool(name, args):
        calls.append(name)
        return BANDED if name == "list_asset_conditions" else RECORDS

    async def run_engine(engine, ask, on_ev):
        raise AssertionError("no engine step here")

    async def replan(question, plan, findings, results):
        assert "s1" in findings and findings["s1"][0]["kind"] == "filter_did_nothing"
        ctx = planner.replan_context(question, plan, findings, results)
        assert "Do not use that tool for that purpose again" in ctx and "61 of 61" in ctx
        return {"mode": "multi", "steps": [{"id": "s1", "kind": "tool", "target": "answer_from_records", "ask": "grade 4 assets with replacement value",
                                            "args": {"question": "grade 4 assets"}, "depends_on": [], "why": "the records hold the grade"}]}

    plan = {"mode": "multi", "steps": [
        {"id": "s1", "kind": "tool", "target": "list_asset_conditions", "ask": "assets with condition grade 4", "args": {}, "depends_on": [], "why": ""},
        {"id": "s2", "kind": "tool", "target": "answer_from_records", "ask": "replacement value of those", "args": {"question": "x"}, "depends_on": ["s1"], "why": ""}]}
    results = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool, question="grade 4 cost", replan=replan))
    assert calls == ["list_asset_conditions", "answer_from_records"]          # s2 of the first plan never ran
    assert results["s1"]["output"] is BANDED and results["rs1"]["output"] is RECORDS
    assert results["__plan__"]["output"]["steps"][0]["id"] == "rs1"


def test_execute_without_a_replanner_or_findings_runs_the_plan_as_before():
    async def run_tool(name, args):
        return RECORDS

    async def run_engine(engine, ask, on_ev):
        return "", []

    plan = {"mode": "single", "steps": [{"id": "s1", "kind": "tool", "target": "answer_from_records", "ask": "grade 4 assets", "args": {"question": "q"}, "depends_on": [], "why": ""}]}
    results = asyncio.run(planner.execute(plan, run_engine=run_engine, run_tool=run_tool))
    assert set(results) == {"s1"} and results["s1"]["ok"]


DECISIONS = {"ok": True, "count": 29, "total": 29, "decisions": [
    {"work_order": "WO-B-301-4562", "state": "Blocked", "source": "Vendors", "trigger": "Work order held at status 'Held'",
     "detail": "Blocked - accreditation: Pennard Fire Services's BAFE SP203-1 has lapsed; " * 3, "asset": "Fire alarm panel",
     "asset_id": "9f9e3253", "building": "Bishopsgate Tower", "vendor": "Pennard Fire Services", "estimated_cost": 650.0, "priority": "P2"}]}


def test_the_digest_keeps_every_scalar_field_so_the_check_sees_what_the_row_holds():
    d = planner._digest(DECISIONS)
    for k in ("work_order=WO-B-301-4562", "state=Blocked", "source=Vendors", "trigger=Work order held", "detail=Blocked - accreditation", "vendor=Pennard"):
        assert k in d, k
    assert "asset_id" not in d and "…" in d          # ids dropped, prose clipped
    assert "A result of the right kind that lacks one optional field" in planner.CHECK_PROMPT


def test_a_table_row_that_says_priority_is_not_a_claim_that_priority_is_unavailable():
    results = {"s1": {"ok": True, "output": DECISIONS}}
    row = "| WO-B-301-4562 | Pennard | Not explicitly recorded | No delay-cost field recorded. Estimated cost: 650.0. Priority: P2. Vendor: Pennard |"
    assert planner.verify_claims(row, "what needs my decision and what does delay cost?", results) == []
    # but a field named right before "unavailable" that the rows hold IS a contradiction
    assert planner.verify_claims("The vendor is unavailable in the records.", "which vendor?", results)
