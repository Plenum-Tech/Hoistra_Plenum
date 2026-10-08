"""Channel 5: a correction that re-ran a turn, or a thumbs-down with a reason, teaches by itself."""
from __future__ import annotations

import uuid

import pytest

from src.agents import auto_teach


def test_a_correction_becomes_a_sentence_for_the_next_similar_question():
    q = "How many work orders were raised at Bishopsgate Tower last month?"
    t = auto_teach.teaching_from_correction(q, {"mode": "query", "text": 'Exclude work orders with status "Cancelled" from "raised".',
                                                "exclude": ["Cancelled"], "period": "this_month", "field": "raised_at"})
    assert t == ('For questions like "How many work orders were raised at Bishopsgate Tower last month": '
                 'Exclude work orders with status "Cancelled" from "raised"; exclude status Cancelled; use the period this month; date by raised_at.')
    assert auto_teach.teaching_from_correction(q, {"mode": "route", "route": "compliance", "text": ""}) == \
        'For questions like "How many work orders were raised at Bishopsgate Tower last month": route it to the compliance engine.'
    assert "call it with period=this_month" in auto_teach.teaching_from_correction(q, {"mode": "tool", "args": {"period": "this_month"}})
    assert auto_teach.teaching_from_correction(q, {"mode": "suggestion", "text": "ok"}) is None
    assert auto_teach.teaching_from_correction("", {"mode": "suggestion", "text": "Count only open jobs."}) == "Count only open jobs."


def test_a_thumbs_down_teaches_only_when_it_says_why():
    q = "which assets are required for repurchase?"
    assert auto_teach.teaching_from_feedback(q, "wrong") is None
    assert auto_teach.teaching_from_feedback(q, "Not right.") is None
    t = auto_teach.teaching_from_feedback(q, "Repurchase means parts below reorder level, not energy recommendations")
    assert t == ('When asked "which assets are required for repurchase" the answer was marked wrong: '
                 "Repurchase means parts below reorder level, not energy recommendations.")
    assert auto_teach.subject_of({"question": "work orders at B-301 last month"}) == "B-301"
    assert auto_teach.subject_of({"question": "work orders"}) is None


@pytest.mark.asyncio
async def test_teach_drops_personal_text_and_never_raises(monkeypatch):
    from src.services import chat_memories as cm
    from src.services.principal import Principal

    stored = []

    async def fake_store(items, *, principal, org, source_thread=None, source_turn=None):
        stored.extend(i["text"] for i in items)
        return len(items)

    monkeypatch.setattr(cm, "store", fake_store)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="user", can_ingest=True, building_ids=None)
    out = await auto_teach.teach(["Count only open jobs.", "Call me on +44 7700 900123 about it"], principal=p, org=str(p.organization_id))
    assert out == ["Count only open jobs."] and stored == ["Count only open jobs."]

    async def boom(items, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(cm, "store", boom)
    assert await auto_teach.teach(["Count only open jobs."], principal=p, org=None) == []


def test_the_rerun_and_feedback_routes_return_what_they_taught(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.agents import trace
    from src.api.deps import get_orchestrator
    from src.api.routes import traces as routes
    from src.services.principal import Principal, current_principal

    app = FastAPI()
    app.include_router(routes.router)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin", can_ingest=True, building_ids=None)
    app.dependency_overrides[current_principal] = lambda: p

    class _Orch:
        async def rerun_turn(self, turn, corrections, *, session_id):
            return {"ok": True, "turn_id": "turn-new", "rerun_of": turn["turn_id"], "answer": "791 raised.", "tool_calls": [], "applied": ["s1: x"]}

    app.dependency_overrides[get_orchestrator] = lambda: _Orch()
    taught = []

    async def fake_teach(texts, *, principal, org, source_thread=None, source_turn=None, subject=None):
        taught.extend(texts)
        return list(texts)

    async def fake_get(principal, org, turn_id):
        return {"turn_id": turn_id, "session_id": "s", "question": "How many work orders were raised at B-301 last month?",
                "spans": [{"id": "root", "seq": 0, "kind": "turn", "name": "turn"},
                          {"id": "r", "seq": 1, "kind": "router", "name": "router", "parent_id": "root", "output": {"model_output": {"agent": "wo_engine"}}}]}

    async def fake_feedback(principal, org, turn_id, rating, comment):
        return True

    monkeypatch.setattr(routes.auto_teach, "teach", fake_teach)
    monkeypatch.setattr(trace, "get_turn", fake_get)
    monkeypatch.setattr(trace, "set_feedback", fake_feedback)
    c = TestClient(app)
    r = c.post("/api/traces/turns/t1/rerun", json={"session_id": "s", "corrections": [{"span_id": "r", "mode": "query", "text": "Exclude Cancelled.", "exclude": ["Cancelled"]}]})
    assert r.status_code == 200
    assert r.json()["taught"] == ['For questions like "How many work orders were raised at B-301 last month": Exclude Cancelled; exclude status Cancelled.']
    r = c.post("/api/traces/turns/t1/feedback", json={"rating": "down", "comment": "It counted cancelled jobs as raised"})
    assert r.json()["taught"] == ['When asked "How many work orders were raised at B-301 last month" the answer was marked wrong: It counted cancelled jobs as raised.']
    assert c.post("/api/traces/turns/t1/feedback", json={"rating": "up"}).json() == {"ok": True, "taught": []}
    assert len(taught) == 2
