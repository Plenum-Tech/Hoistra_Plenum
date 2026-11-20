"""Memorilla as the one place recall reads from (services/memorilla.py, 20 Nov 2026).

With PLENUM_MEMORY_URL set: memories are ranked by the Memorilla server but only ones the caller
may see are recalled; recall is a "memory recall" span either way; a past plan is reused only when
its question is close and clearly closer than any other plan, and only when the planner's own
checks still pass; a good turn's plan is kept for next time, a recalled or follow-up one is not.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest

from src.agents import trace
from src.http_client import caller_organization_id
from src.services import chat_memories as cm
from src.services import memorilla
from src.services.principal import Principal, caller_principal

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
ORG = "ad88d1ed-4376-447c-8196-e4e5b81d5af6"


class _Result:
    def __init__(self, rows=None):
        self._rows, self.rowcount = rows or [], len(rows or [])

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Conn:
    def __init__(self, store):
        self.store = store

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        sql = " ".join(str(stmt).split())
        self.store["sql"].append((sql, params or {}))
        if "FROM plenum_cafm.chat_memories WHERE" in sql:
            return _Result(self.store["mems"])
        return _Result([])


class _Engine:
    def __init__(self, store):
        self.store = store

    def begin(self):
        return _Conn(self.store)

    def connect(self):
        return _Conn(self.store)


def _mem(mid, text, subject=None, kind="fact"):
    return {"id": mid, "kind": kind, "text": text, "subject": subject, "embedding": None, "user_id": None,
            "created_by_email": "ops@example.com", "created_at": NOW}


@pytest.fixture
def db(monkeypatch):
    store = {"sql": [], "mems": [], "hits": [], "searched": [], "fail": False, "added": []}
    import src.database as database

    monkeypatch.setattr(database, "_get_engine", lambda: _Engine(store))
    monkeypatch.setattr(cm, "_ready", True)
    monkeypatch.setenv("PLENUM_MEMORY_URL", "http://127.0.0.1:8000")

    async def embed(texts):
        return None

    async def search(user, question, k):
        store["searched"].append((user, question, k))
        if store["fail"]:
            raise RuntimeError("connection refused")
        return store["hits"]

    async def add(user, texts):
        store["added"].append((user, texts))
        return len(texts)

    monkeypatch.setattr(cm, "embed", embed)
    monkeypatch.setattr(memorilla, "search", search)
    monkeypatch.setattr(memorilla, "add", add)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.UUID(ORG), role="admin",
                  can_ingest=True, building_ids=None)
    caller_principal.set(p)
    caller_organization_id.set(ORG)
    trace._turn.set(None)
    return store


ASBESTOS = "For B-101, the asbestos survey is from 2019 and identifies asbestos in the ceilings."
APEX = "At Bishopsgate Tower, Apex Mechanical must not be instructed for AHU-7 until its SSIP issue is cleared."


def test_memories_are_ranked_by_memorilla_and_only_visible_ones_are_recalled(db):
    db["mems"] = [_mem("m1", ASBESTOS, subject="B-101"), _mem("m2", APEX)]
    db["hits"] = [
        {"similarity": 0.81, "text": "[company fact] Someone else's note that this caller may not see."},
        {"similarity": 0.62, "text": memorilla.memory_text("fact", APEX)},
        {"similarity": 0.52, "text": memorilla.memory_text("fact", ASBESTOS, "B-101")},
    ]
    hits = asyncio.run(cm.recall("Can Apex fix AHU-7 at Bishopsgate Tower?"))
    assert [h["id"] for h in hits] == ["m2", "m1"]
    assert db["searched"][0][0] == "hoistra-ad88d1ed-memories"
    used = [p for s, p in db["sql"] if "use_count = use_count + 1" in s]
    assert used and used[0]["ids"] == ["m2", "m1"]


def test_a_memory_below_the_memorilla_floor_is_not_recalled(db):
    db["mems"] = [_mem("m1", ASBESTOS)]
    db["hits"] = [{"similarity": memorilla.MEMORY_MIN - 0.01, "text": memorilla.memory_text("fact", ASBESTOS)}]
    assert asyncio.run(cm.recall("What is the boiler pressure at the depot?")) == []


def test_memorilla_down_falls_back_to_the_local_ranking(db):
    db["mems"] = [_mem("m1", ASBESTOS, subject="B-101")]
    db["fail"] = True
    hits = asyncio.run(cm.recall("Plan a lighting upgrade at B-101 ceilings asbestos"))
    assert [h["id"] for h in hits] == ["m1"]


def test_recall_is_one_memory_span_with_its_path_and_what_was_recalled(db):
    async def run():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-m", "session_id": "s1",
                           "summary": "q", "payload": {"message": "Plan a lighting upgrade at B-101."}})
        db["mems"] = [_mem("m1", ASBESTOS, subject="B-101")]
        db["hits"] = [{"similarity": 0.52, "text": memorilla.memory_text("fact", ASBESTOS, "B-101")}]
        await cm.recall("Plan a lighting upgrade at B-101.")
        return trace.current()

    monkey_ready = trace._ready
    trace._ready = True
    try:
        t = asyncio.run(run())
    finally:
        trace._ready = monkey_ready
    span = next(s for s in t.spans if s.kind == "memory")
    assert span.name == "memory recall" and span.parent_id == t.root.id and not span.open
    assert span.input["path"] == "memorilla"
    assert span.output["recalled"][0]["id"] == "m1" and span.output["marked_used"] == 1


# ── plans ──────────────────────────────────────────────────────────────────────────────

def _plan_hit(sim, q, plan="(single): s1: engine compliance"):
    return {"similarity": sim, "text": f"Question: {q} | Plan {plan}"}


def test_a_plan_is_reused_only_when_close_and_clearly_closer_than_any_other():
    top, why = memorilla.choose_plan_hit([_plan_hit(0.68, "What needs my approval today?"),
                                          _plan_hit(0.56, "Lapsed certificates and overdue work orders?", "(multi): s1: engine compliance; s2: engine wo_engine")])
    assert top is not None and why == "ok"
    top, why = memorilla.choose_plan_hit([_plan_hit(0.60, "What needs my approval today?")])
    assert top is None and "below" in why
    top, why = memorilla.choose_plan_hit([_plan_hit(0.79, "List all open work orders by building."),
                                          _plan_hit(0.77, "Which work orders are late?", "(single): s1: engine wo_engine")])
    assert top is None and "margin" in why
    # the same plan twice (the same question asked twice) is not a rival
    top, why = memorilla.choose_plan_hit([_plan_hit(0.88, "How many work orders were raised in August?"),
                                          _plan_hit(0.87, "How many work orders were raised in August 2026?")])
    assert top is not None


def test_recall_plan_reads_the_past_turns_plan_and_checks_it_again(db, monkeypatch):
    plan = {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance", "depends_on": []}], "areas": ["compliance"]}
    db["hits"] = [_plan_hit(0.68, "What needs my approval today?")]

    async def plan_of_question(org, q):
        assert q == "What needs my approval today?" and org == ORG
        return plan, "turn-abc", {"rules": memorilla.rules_fingerprint(), "started_at": None}

    monkeypatch.setattr(memorilla, "plan_of_question", plan_of_question)
    seen = []
    got, info = asyncio.run(memorilla.recall_plan(ORG, "What is waiting for my approval today?",
                                                   validate=lambda p: (seen.append(p) or p, None)))
    assert got["steps"][0]["target"] == "compliance" and info["used"] and info["from_turn"] == "turn-abc"
    assert "areas" not in seen[0]  # the planner recomputes it
    got, info = asyncio.run(memorilla.recall_plan(ORG, "What is waiting for my approval today?",
                                                   validate=lambda p: (None, "engine 'compliance' is not part of this space")))
    assert got is None and not info["used"] and "no longer validates" in info["why"]


def test_a_one_engine_plan_from_the_router_is_checked_by_its_engine_not_by_the_planners_ask():
    one = {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": None, "depends_on": []}]}
    refuse = lambda p: (None, "step s1: no ask")  # noqa: E731 - what planner.validate says of it
    got, why = memorilla.check_plan(one, validate=refuse, engines={"compliance", "wo_engine"}, allowed={"compliance"})
    assert got is one and why is None
    got, why = memorilla.check_plan(one, validate=refuse, engines={"compliance"}, allowed={"wo_engine"})
    assert got is None and "not part of this space" in why
    two = {"steps": [one["steps"][0], {"id": "s2", "kind": "engine", "target": "wo_engine"}]}
    got, why = memorilla.check_plan(two, validate=refuse, engines={"compliance", "wo_engine"}, allowed={"compliance", "wo_engine"})
    assert got is None and why == "step s1: no ask"


PAST_TOOLS = [
    {"tool": "list_building_certificates", "engine": "compliance", "args": {"building_name": "Bishopsgate Tower", "limit": 200}},
    {"tool": "list_building_certificates", "engine": "compliance", "args": {"building_name": "Bishopsgate Tower", "limit": 200}},
    {"tool": "answer_from_records", "engine": "maintenance",
     "args": {"question": "How many work orders were raised at Bishopsgate Tower between 2026-08-01 and 2026-08-31?"}},
    {"tool": "list_vendor_accreditations", "engine": "compliance", "args": {}},
]


def test_the_recipe_names_only_this_engines_tools_in_order_once_each():
    block, calls = memorilla.recipe_block(PAST_TOOLS, ["list_building_certificates", "list_vendor_accreditations"],
                                          "Which certificates have lapsed at Bishopsgate Tower?")
    assert [c["tool"] for c in calls] == ["list_building_certificates", "list_vendor_accreditations"]
    assert block.index("1. list_building_certificates(") < block.index("2. list_vendor_accreditations(")
    assert '"Which certificates have lapsed at Bishopsgate Tower?"' in block
    assert "Set every argument from THIS question" in block


def test_a_recipe_never_tells_the_engine_to_reuse_an_earlier_period():
    block, calls = memorilla.recipe_block(PAST_TOOLS, ["answer_from_records"])
    assert calls[0]["args"]["question"].endswith("2026-08-31?")   # shown as the earlier question's
    assert "never copy a value from the earlier one that this question does not state" in block


def test_no_recipe_when_none_of_the_past_tools_are_this_engines():
    assert memorilla.recipe_block(PAST_TOOLS, ["get_energy_overview"]) == ("", [])
    assert memorilla.recipe_block([], ["list_building_certificates"]) == ("", [])


def test_a_recipe_is_capped_and_long_arguments_are_cut():
    many = [{"tool": "t", "args": {"n": i, "text": "x" * 500}} for i in range(20)]
    block, calls = memorilla.recipe_block(many, ["t"])
    assert len(calls) == memorilla.RECIPE_MAX and " …" in block


def test_the_past_turns_tools_come_with_the_reused_plan(db, monkeypatch):
    plan = {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": None}]}
    db["hits"] = [_plan_hit(0.79, "Which certificates have lapsed at Bishopsgate Tower?")]

    async def plan_of_question(org, q):
        return plan, "turn-6aee", {"rules": memorilla.rules_fingerprint(), "started_at": None}

    async def tools_of_turn(turn_id):
        assert turn_id == "turn-6aee"
        return PAST_TOOLS[:1]

    monkeypatch.setattr(memorilla, "plan_of_question", plan_of_question)
    monkeypatch.setattr(memorilla, "tools_of_turn", tools_of_turn)
    got, info = asyncio.run(memorilla.recall_plan(ORG, "Which certificates at Bishopsgate Tower have expired?",
                                                   validate=lambda p: (p, None)))
    assert got == plan and info["tools"] == PAST_TOOLS[:1]


def test_tools_of_turn_skips_the_loops_own_plumbing(monkeypatch):
    rows = [("task", "tool:meta", {"description": "x"}), ("select_skill", "orchestrator loop", {}),
            ("list_compliance_approvals", "compliance", '{"status": "pending"}')]

    class _R:
        def all(self):
            return rows

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, params=None):
            assert "kind = 'tool'" in str(stmt) and params == {"t": "turn-1"}
            return _R()

    import src.database as database

    monkeypatch.setattr(database, "_get_engine", lambda: type("E", (), {"connect": lambda self: _C()})())
    got = asyncio.run(memorilla.tools_of_turn("turn-1"))
    assert got == [{"tool": "list_compliance_approvals", "engine": "compliance", "args": {"status": "pending"}}]


def test_a_stamped_plan_is_reused_only_under_the_same_rules():
    now = "abc123def456"
    assert memorilla.stale_reason(now, None, current=now, since=None) is None
    why = memorilla.stale_reason("000000000000", None, current=now, since="2026-01-01")
    assert why and "routing rules changed" in why          # a date never revives a plan made under other rules


def test_an_unstamped_plan_needs_plenum_plans_since_and_a_turn_after_it():
    t = datetime(2026, 11, 12, 8, 22, tzinfo=timezone.utc)
    assert "not set" in memorilla.stale_reason(None, t, current="x", since=None)
    assert "older than" in memorilla.stale_reason(None, t, current="x", since="2026-11-14")
    assert memorilla.stale_reason(None, t, current="x", since="2026-11-12") is None
    assert memorilla.stale_reason(None, "2026-11-17T07:22:00+00:00", current="x", since="2026-11-14") is None
    assert "not a date" in memorilla.stale_reason(None, t, current="x", since="last tuesday")


def test_a_plan_from_before_a_rules_change_is_not_reused(db, monkeypatch):
    plan = {"mode": "single", "steps": [{"id": "s1", "kind": "engine", "target": "compliance", "ask": None}]}
    db["hits"] = [_plan_hit(0.77, "Which contracts come up for renewal in the next 90 days?")]

    async def plan_of_question(org, q):
        return plan, "turn-d1fd", {"rules": None, "started_at": datetime(2026, 11, 12, 8, 22, tzinfo=timezone.utc)}

    monkeypatch.setattr(memorilla, "plan_of_question", plan_of_question)
    monkeypatch.setenv("PLENUM_PLANS_SINCE", "2026-11-14")
    got, info = asyncio.run(memorilla.recall_plan(ORG, "Which contracts need renewing in the next three months?",
                                                   validate=lambda p: (p, None)))
    assert got is None and not info["used"] and "older than PLENUM_PLANS_SINCE" in info["why"]
    assert info["rules"]["plan"] is None and info["rules"]["now"] == memorilla.rules_fingerprint()


def test_every_plan_span_is_stamped_with_todays_rules(db):
    async def run():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-r", "session_id": "s1",
                           "summary": "q", "payload": {"message": "Which certificates have lapsed?"}})
        trace.on_plan({"steps": [{"id": "s1", "kind": "engine", "target": "compliance"}]}, source="router")
        return trace.current()

    prev = trace._ready
    trace._ready = True
    try:
        t = asyncio.run(run())
    finally:
        trace._ready = prev
    span = next(s for s in t.spans if s.kind == "plan")
    assert span.input["rules"] == memorilla.rules_fingerprint() and len(span.input["rules"]) == 12


def test_recall_plan_never_raises(db):
    db["fail"] = True
    got, info = asyncio.run(memorilla.recall_plan(ORG, "anything", validate=lambda p: (p, None)))
    assert got is None and info["used"] is False and "connection refused" in info["error"]


def _turn_with_plan(question, plan, source="router", ok=True):
    trace._turn.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-" + uuid.uuid4().hex[:8],
                       "session_id": "s1", "summary": question, "payload": {"message": question}})
    t = trace.current()
    t.organization_id = ORG
    trace.on_plan(plan, source=source)
    if not ok:
        t.root.ok = False
    return t


@pytest.mark.parametrize("question,plan,source,ok,kept", [
    ("Which vendors are slipping on SLA this month?", {"steps": [{"id": "s1", "kind": "engine", "target": "contract_performance"}]}, "router", True, True),
    ("Which vendors are slipping on SLA this month?", {"steps": [{"id": "s1", "kind": "engine", "target": "contract_performance"}]}, "memorilla", True, False),
    ("all of them please", {"steps": [{"id": "s1", "kind": "loop", "target": "orchestrator loop"}]}, "router", True, False),
    ("Show me everything that's at risk", {"steps": [{"id": "s1", "kind": "loop", "target": "clarify"}]}, "router", True, False),
    ("Which vendors are slipping on SLA this month?", {"steps": [{"id": "s1", "kind": "engine", "target": "contract_performance"}]}, "router", False, False),
])
def test_a_good_turns_plan_is_kept_for_next_time(db, question, plan, source, ok, kept):
    async def run():
        t = _turn_with_plan(question, plan, source, ok)
        await trace._index_plan(t)

    prev = trace._ready
    trace._ready = True
    try:
        asyncio.run(run())
    finally:
        trace._ready = prev
    assert bool(db["added"]) is kept
    if kept:
        user, texts = db["added"][0]
        assert user == "hoistra-ad88d1ed-plans" and texts[0].startswith(f"Question: {question} | Plan")


def test_a_new_memory_is_also_kept_in_memorilla(db):
    p = caller_principal.get()
    n = asyncio.run(cm.store([{"kind": "fact", "text": ASBESTOS, "subject": "B-101"},
                              {"kind": "preference", "text": "The user prefers costs in GBP."}], principal=p, org=ORG))
    assert n == 2
    texts = [t for _, ts in db["added"] for t in ts]
    assert texts == [memorilla.memory_text("fact", ASBESTOS, "B-101"),
                     memorilla.memory_text("preference", "The user prefers costs in GBP.", personal=True)]
    assert all(u == "hoistra-ad88d1ed-memories" for u, _ in db["added"])


def test_off_without_the_url(monkeypatch, db):
    monkeypatch.delenv("PLENUM_MEMORY_URL")
    db["mems"] = [_mem("m1", ASBESTOS, subject="B-101")]
    asyncio.run(cm.recall("Plan a lighting upgrade at B-101 ceilings asbestos"))
    assert db["searched"] == []
