"""The skill lab keeps an instruction change only when replayed questions say it helps, and a replay
changes nothing.

A replay answers a real question through the real chat path, so the guards are what keep it from
writing a trace billed to the company, a chat thread, chat memories learned from its own answer,
or - for a question that asks for an action - a work order or an email.
"""
from __future__ import annotations

import asyncio

import pytest

from src.agents import context_budget, replay_guard, skill_lab
from src.services import skill_overlays


# ── the test set ─────────────────────────────────────────────────────────────────────────

def test_a_question_stays_in_one_split_whatever_its_spacing_or_case():
    q = "Which fire certificates have lapsed at Bishopsgate?"
    assert skill_lab.split_of(q) == skill_lab.split_of("  which FIRE certificates have lapsed   at bishopsgate? ")
    splits = {skill_lab.split_of(f"question number {i} about buildings") for i in range(30)}
    assert splits == {"train", "held_out"}


@pytest.mark.parametrize("q", [
    "Raise a work order for the boiler", "Send the compliance pack to Hussain", "Approve WO-1234",
    "Schedule the lift inspection", "remember that Apex maintains the lifts", "hi",
])
def test_questions_that_ask_for_an_action_or_say_nothing_are_never_replayed(q):
    assert not skill_lab.usable(q)


def test_a_question_that_only_reads_is_replayed():
    assert skill_lab.usable("Which fire certificates have lapsed at Bishopsgate Tower?")


# ── the rule a rewrite must pass ─────────────────────────────────────────────────────────

def _s(score, tokens, n=6):
    return {"score": score, "input_tokens": tokens, "questions": n}


def test_as_complete_and_cheaper_is_proposed():
    ok, why = skill_lab.better(_s(0.95, 30000), _s(0.94, 22000))
    assert ok and "fewer tokens" in why


def test_cheaper_but_losing_facts_is_not():
    ok, why = skill_lab.better(_s(0.95, 30000), _s(0.85, 15000))
    assert not ok and "not better" in why


def test_clearly_more_complete_is_proposed_even_if_it_costs_more():
    assert skill_lab.better(_s(0.80, 20000), _s(0.90, 26000))[0]


def test_nothing_scored_is_never_proposed():
    assert not skill_lab.better(_s(None, 1), _s(0.99, 0))[0]


def test_summaries_ignore_failed_replays_and_count_compactions():
    rows = [
        {"variant": "current", "score": 1.0, "usd": 0.1, "input_tokens": 20000, "peak_prompt_tokens": 9000,
         "context": {"compactions": 2, "refused": 1, "shrunk": 0}},
        {"variant": "current", "score": 0.5, "usd": 0.2, "input_tokens": 30000, "peak_prompt_tokens": 12000,
         "context": {"compactions": 0, "refused": 0, "shrunk": 1}},
        {"variant": "current", "error": "boom"},
        {"variant": "reference", "score": 1.0, "usd": 0.3, "input_tokens": 40000, "peak_prompt_tokens": 20000},
    ]
    s = skill_lab.summarise(rows, "current")
    assert (s["questions"], s["score"], s["input_tokens"], s["compactions"], s["cut_to_fit"], s["errors"]) == \
        (2, 0.75, 25000.0, 2, 1, 1)


def test_the_proposer_is_told_what_was_lost_and_what_failed():
    rows = [{"variant": "current", "score": 0.6,
             "judge": {"missing": ["FRA expiry 2026-06-29"], "contradicted": []},
             "context": {"compactions": 0, "refused": 1, "shrunk": 2, "peak_working_tokens": 30000}}]
    ev = skill_lab.evidence_text(rows)
    assert "lost: FRA expiry 2026-06-29" in ev and "1 compaction(s) refused" in ev
    assert "2 result(s) cut to fit" in ev and "never compacted" in ev


# ── a replay ─────────────────────────────────────────────────────────────────────────────

class _Orchestrator:
    """Records what the world looked like while it answered."""

    def __init__(self):
        self.seen = {}

    async def run_stateful(self, *, user_message, session_id):
        from src.http_client import caller_authorization, caller_organization_id
        self.seen = {
            "replay": replay_guard.is_replay(),
            "mode": context_budget.MODE.get(),
            "candidate": skill_overlays.text_for("query-builder", "context-budget"),
            "org": caller_organization_id.get(),
            "auth": caller_authorization.get(),
            "session": session_id,
        }
        context_budget.STATS.get()["compactions"] += 1
        return {"answer": "Two fire certificates have lapsed.", "error": None}


def test_a_replay_runs_guarded_with_the_text_under_test_and_leaves_nothing_set():
    orch = _Orchestrator()
    out = asyncio.run(skill_lab.replay(
        orch, "Which fire certificates have lapsed?", run_id="0123456789abcdef", idx=3, variant="proposed",
        mode="self", candidate={("query-builder", "context-budget"): "TRIAL TEXT"}, principal=None,
        organization_id="org-1", authorization="Bearer t"))
    assert orch.seen["replay"] and orch.seen["mode"] == "self" and orch.seen["candidate"] == "TRIAL TEXT"
    assert (orch.seen["org"], orch.seen["auth"]) == ("org-1", "Bearer t")
    assert orch.seen["session"].startswith("skilllab-01234567-3-proposed-")
    assert out["answer"] == "Two fire certificates have lapsed." and out["context"]["compactions"] == 1
    # Nothing leaks into whatever runs next in this process.
    assert not replay_guard.is_replay() and context_budget.MODE.get() is None
    assert skill_overlays.CANDIDATE.get() is None


def test_a_replay_that_raises_is_a_result_not_a_failed_run():
    class Boom:
        async def run_stateful(self, **_):
            raise RuntimeError("engine down")

    out = asyncio.run(skill_lab.replay(Boom(), "q?", run_id="r" * 16, idx=0, variant="current", mode="self",
                                       candidate=None, principal=None, organization_id=None, authorization=None))
    assert out["answer"] == "" and "engine down" in out["error"]
    assert not replay_guard.is_replay()


# ── the guards themselves ────────────────────────────────────────────────────────────────

def test_during_a_replay_no_write_request_leaves_the_process():
    from src import http_client

    async def go():
        token = replay_guard.REPLAY.set(True)
        try:
            await http_client.request("POST", "http://x", "/api/work-orders", service="wo")
        finally:
            replay_guard.REPLAY.reset(token)

    with pytest.raises(replay_guard.ReplayRefusedWrite):
        asyncio.run(go())


def test_during_a_replay_no_trace_and_no_memory_is_written():
    from src.agents import trace
    from src.services import chat_memories

    token = replay_guard.REPLAY.set(True)
    try:
        assert trace.begin("turn-x", "skilllab-x") is None
        chat_memories.learn_soon("skilllab-x", "q", "a")   # returns without scheduling anything
    finally:
        replay_guard.REPLAY.reset(token)


def test_a_candidate_text_reaches_only_the_replay_that_set_it():
    assert skill_overlays.text_for("query-builder", "context-budget") in (None, skill_overlays._active.get(
        ("query-builder", "context-budget")))
    token = skill_overlays.CANDIDATE.set({("query-builder", "context-budget"): "X"})
    try:
        assert skill_overlays.doc("query-builder", "context-budget") == "X"
    finally:
        skill_overlays.CANDIDATE.reset(token)
    assert "compact_context" in skill_overlays.doc("query-builder", "context-budget")   # the shipped file


def test_only_documents_read_on_every_step_can_be_tuned():
    assert skill_lab.TUNABLE == {("query-builder", "context-budget")}


# ── the endpoints ────────────────────────────────────────────────────────────────────────

def test_only_an_administrator_can_start_a_run_or_approve():
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.routes import skill_lab as routes
    from src.services import principal as P

    app = FastAPI()
    app.include_router(routes.router)
    plain = P.Principal(user_id=uuid4(), email="u@example.com", organization_id=uuid4(), role="user",
                        can_ingest=False, building_ids=())
    app.dependency_overrides[P.current_principal] = lambda: plain
    c = TestClient(app)
    assert c.post("/api/skill-lab/runs", json={"kind": "compare", "sample": 2}).status_code == 403
    assert c.post(f"/api/skill-lab/proposals/{uuid4()}/approve").status_code == 403
    assert c.get("/api/skill-lab/runs").status_code == 403
