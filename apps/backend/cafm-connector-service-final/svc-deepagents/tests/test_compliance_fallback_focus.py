"""The engine-tool fallback reads what the plan asked for (7 Oct 2026).

When the direct register read fails, the compliance shortcut answers from the engine tools.
It used to read the first 200 vendor and the first 200 building certificates whatever the
question, and the context budget then shed rows from the front - the vendor list before the
building one. So "Which vendors are blocked right now?" named three of the five blocked
vendors the dashboard counted, and said the other two were "in rows dropped from this
extract", while every one of 200 building certificates reached the analyst.
"""
from __future__ import annotations

import asyncio
import json

from src.agents import compliance_engine_agent as cea
from src.agents.orchestrator import DeepAgentOrchestrator as O


def _plan(*subs, logic=None):
    return {"sub_questions": [
        {"id": f"q{i + 1}", "text": "t", "query": {
            "scope": scope,
            "filters": [{"field": f, "op": op, "value": v} for f, op, v in filters],
            **({"logic": logic} if logic else {}),
        }}
        for i, (scope, filters) in enumerate(subs)
    ]}


BLOCKED = ("vendor", [("vendor_block_state", "eq", "Blocked")])


class TestWhatTheEngineCanFilter:
    def test_a_blocked_vendor_question_reads_the_blocked_vendors(self):
        assert O._engine_tool_focus(_plan(BLOCKED)) == {"vendor": {"risk_filter": "blocked"}}

    def test_a_status_filter_is_passed_through(self):
        assert O._engine_tool_focus(_plan(("building", [("status", "eq", "Lapsed")]))) == {
            "building": {"status": "Lapsed"}}

    def test_a_question_on_both_registers_narrows_both(self):
        assert O._engine_tool_focus(_plan(("both", [("status", "eq", "Lapsed")]))) == {
            "vendor": {"status": "Lapsed"}, "building": {"status": "Lapsed"}}

    def test_a_vendor_block_never_narrows_the_building_register(self):
        # "Which buildings do blocked vendors sign for" needs those buildings' certificates
        # whatever their own state; the engine's blocked filter would keep only lapsed ones.
        plan = _plan(("both", [("vendor_block_state", "eq", "Blocked")]))
        assert O._engine_tool_focus(plan) == {"vendor": {"risk_filter": "blocked"}}

    def test_two_conditions_that_must_both_hold_are_both_applied(self):
        plan = _plan(("vendor", [("vendor_block_state", "eq", "blocked"), ("status", "eq", "Lapsed")]))
        assert O._engine_tool_focus(plan) == {"vendor": {"risk_filter": "blocked", "status": "Lapsed"}}

    def test_either_or_is_never_narrowed(self):
        plan = _plan(("vendor", [("vendor_block_state", "eq", "Blocked"), ("status", "eq", "Lapsed")]), logic="or")
        assert O._engine_tool_focus(plan) == {}

    def test_a_filter_the_engine_cannot_apply_leaves_the_register_whole(self):
        assert O._engine_tool_focus(_plan(("vendor", [("forensics_verdict", "eq", "forged")]))) == {}
        assert O._engine_tool_focus(_plan(("vendor", [("vendor_block_state", "eq", "Clear")]))) == {}

    def test_a_second_question_on_the_same_register_keeps_it_whole(self):
        # q2 needs rows q1's filter would drop, and one read cannot serve both.
        plan = _plan(BLOCKED, ("vendor", [("days_to_expiry", "lte", 30)]))
        assert O._engine_tool_focus(plan) == {}

    def test_no_plan_no_focus(self):
        assert O._engine_tool_focus({}) == {}
        assert O._engine_tool_focus(None) == {}


class _Tool:
    def __init__(self, calls, name, answer):
        self.calls, self.name, self.answer = calls, name, answer

    async def ainvoke(self, args):
        self.calls.append((self.name, dict(args)))
        return self.answer(args)


def _patch(monkeypatch, vendor, building):
    calls: list = []
    monkeypatch.setattr(cea, "get_compliance_saved_space_summary",
                        _Tool(calls, "summary", lambda a: {"ok": True, "vendors": {"blocked": 5}}))
    monkeypatch.setattr(cea, "list_vendor_accreditations", _Tool(calls, "vendor", vendor))
    monkeypatch.setattr(cea, "list_building_certificates", _Tool(calls, "building", building))
    return calls


class TestTheFallbackFetch:
    def test_the_blocked_question_asks_the_engine_for_the_blocked_vendors(self, monkeypatch):
        calls = _patch(monkeypatch,
                       vendor=lambda a: {"certificates": [{"id": f"v{i}"} for i in range(5)]},
                       building=lambda a: {"certificates": []})
        notes: list = []
        tcs = asyncio.run(O.__new__(O)._register_via_engine_tools(_plan(BLOCKED), notes))
        assert ("vendor", {"limit": 200, "risk_filter": "blocked"}) in calls
        assert ("building", {"limit": 200}) in calls, "the other register is still read whole"
        assert [t["tool"] for t in tcs] == [
            "get_compliance_saved_space_summary", "list_vendor_accreditations", "list_building_certificates"]
        assert tcs[1]["input"] == {"limit": 200, "risk_filter": "blocked"}
        assert notes == []

    def test_a_question_with_no_engine_filter_reads_as_before(self, monkeypatch):
        calls = _patch(monkeypatch, vendor=lambda a: {"certificates": []}, building=lambda a: {"certificates": []})
        asyncio.run(O.__new__(O)._register_via_engine_tools({}, []))
        assert ("vendor", {"limit": 200}) in calls and ("building", {"limit": 200}) in calls

    def test_a_filtered_read_that_finds_nothing_still_hands_over_the_register(self, monkeypatch):
        calls = _patch(monkeypatch,
                       vendor=lambda a: {"certificates": [] if "risk_filter" in a else [{"id": "v1"}]},
                       building=lambda a: {"certificates": []})
        notes: list = []
        tcs = asyncio.run(O.__new__(O)._register_via_engine_tools(_plan(BLOCKED), notes))
        assert [c for c in calls if c[0] == "vendor"] == [
            ("vendor", {"limit": 200, "risk_filter": "blocked"}), ("vendor", {"limit": 200})]
        vendor_call = next(t for t in tcs if t["tool"] == "list_vendor_accreditations")
        assert vendor_call["input"] == {"limit": 200} and vendor_call["output"]["certificates"] == [{"id": "v1"}]
        assert len(notes) == 1 and "blocked" in notes[0]["dropped"] and notes[0]["kept"], \
            "the analyst is told the rows are wider than the question, so it answers 'none'"

    def test_none_is_only_claimed_when_every_narrowed_register_is_empty(self, monkeypatch):
        # "Which certificates have lapsed?" narrows both registers. No vendor accreditation has
        # lapsed but four building certificates have: the analyst must not be told the answer
        # is NONE with those four in front of it, nor handed current vendor rows as if lapsed.
        calls = _patch(monkeypatch,
                       vendor=lambda a: {"certificates": [] if "status" in a else [{"id": "v-current"}]},
                       building=lambda a: {"certificates": [{"id": f"b{i}"} for i in range(4)]})
        notes: list = []
        tcs = asyncio.run(O.__new__(O)._register_via_engine_tools(
            _plan(("both", [("status", "eq", "Lapsed")])), notes))
        assert notes == []
        vendor_call = next(t for t in tcs if t["tool"] == "list_vendor_accreditations")
        assert vendor_call["input"] == {"limit": 200, "status": "Lapsed"}
        assert vendor_call["output"]["certificates"] == [], "no lapsed accreditation is the vendor answer"
        assert [c for c in calls if c[0] == "vendor"] == [("vendor", {"limit": 200, "status": "Lapsed"})]

    def test_when_every_narrowed_register_is_empty_each_is_handed_over_whole(self, monkeypatch):
        _patch(monkeypatch,
               vendor=lambda a: {"certificates": [] if "status" in a else [{"id": "v1"}]},
               building=lambda a: {"certificates": [] if "status" in a else [{"id": "b1"}]})
        notes: list = []
        tcs = asyncio.run(O.__new__(O)._register_via_engine_tools(
            _plan(("both", [("status", "eq", "Lapsed")])), notes))
        assert len(notes) == 2 and all("status Lapsed" in n["dropped"] for n in notes)
        assert {t["tool"]: t["input"] for t in tcs if t["tool"] != "get_compliance_saved_space_summary"} == {
            "list_vendor_accreditations": {"limit": 200}, "list_building_certificates": {"limit": 200}}

    def test_a_filtered_read_that_errors_falls_back_without_claiming_none(self, monkeypatch):
        _patch(monkeypatch,
               vendor=lambda a: {"error": "boom"} if "risk_filter" in a else {"certificates": [{"id": "v1"}]},
               building=lambda a: {"certificates": []})
        notes: list = []
        tcs = asyncio.run(O.__new__(O)._register_via_engine_tools(_plan(BLOCKED), notes))
        assert next(t for t in tcs if t["tool"] == "list_vendor_accreditations")["input"] == {"limit": 200}
        assert notes == []


class TestTheAskedForRowsSurviveTheBudget:
    def test_a_filtered_read_is_shed_last(self):
        blocked = {"certificates": [
            {"id": f"blocked-{i}", "vendor_name": f"Vendor {i}", "filler": "z" * 3000} for i in range(5)]}
        fat = {"certificates": [{"id": f"b-{i}", "filler": "y" * 2600} for i in range(60)]}
        tcs = [
            {"tool": "get_compliance_saved_space_summary", "input": {}, "output": {"ok": True}},
            {"tool": "list_vendor_accreditations", "input": {"limit": 200, "risk_filter": "blocked"}, "output": blocked},
            {"tool": "list_building_certificates", "input": {"limit": 200}, "output": fat},
        ]
        assert len(json.dumps(tcs)) > 90000, "fixture must actually exceed the budget"
        out = O.__new__(O)._compliance_data_json(tcs)
        json.loads(out)
        for i in range(5):
            assert f"blocked-{i}" in out, "a row the question asked for was dropped to fit"

    def test_unfiltered_reads_still_shed_in_order(self):
        fat = lambda p, n: {"certificates": [{"id": f"{p}-{i}", "filler": "y" * 2600} for i in range(n)]}
        tcs = [
            {"tool": "list_vendor_accreditations", "input": {"limit": 200}, "output": fat("v", 40)},
            {"tool": "list_building_certificates", "input": {"limit": 200}, "output": fat("b", 10)},
        ]
        out = O.__new__(O)._compliance_data_json(tcs)
        assert '"v-39"' not in out and '"b-9"' in out, "with nothing asked for, the order is unchanged"
