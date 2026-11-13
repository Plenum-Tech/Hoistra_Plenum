"""Gaps an independent review found in the chat's side of the access-role boundary (13 Nov 2026).

Each was a way a person held to some pages could still be given another page's records by the
chat: a read made straight from the database inside the compliance pipeline, the conversation
state the loop keeps for itself, a replay of an old turn, an unreadable cut-off read as none.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.agents import meta_tools, orchestrator, page_access as PA
from src.api.routes import traces as traces_routes
from src.services import principal as P

CUT = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)
COMPLIANCE = frozenset({"compliance", "buildings"})


def caller(pages=None, narrowed_at=None):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=str(uuid4()), role="user",
                       can_ingest=False, building_ids=None, pages=pages, access_narrowed_at=narrowed_at)


@pytest.fixture(autouse=True)
def _signed_out():
    token = P.caller_principal.set(None)
    pages = PA.active_pages.set(PA.FROM_CALLER)
    yield
    P.caller_principal.reset(token)
    PA.active_pages.reset(pages)


# ── the conversation the loop keeps for itself ────────────────────────────────

def test_a_thread_is_its_own_for_everyone_without_a_cut():
    for who in (None, caller(), caller(COMPLIANCE)):
        P.caller_principal.set(who)
        assert PA.thread_key("s1") == "s1"


def test_a_narrowed_caller_continues_on_a_fresh_thread():
    # The loop's saved messages hold the tool results of earlier turns. After a narrowing they
    # may be another page's, so the same session carries on under a key of its own.
    P.caller_principal.set(caller(COMPLIANCE, CUT))
    assert PA.thread_key("s1") == "s1@" + str(int(CUT.timestamp()))
    P.caller_principal.set(caller(COMPLIANCE, CUT + timedelta(days=1)))
    assert PA.thread_key("s1") != "s1@" + str(int(CUT.timestamp()))
    assert PA.thread_key("") == "" and PA.thread_key(None) is None


def test_the_loop_runs_on_that_thread():
    orch = orchestrator.DeepAgentOrchestrator.__new__(orchestrator.DeepAgentOrchestrator)
    P.caller_principal.set(caller(COMPLIANCE, CUT))
    assert orch._config("s1")["configurable"]["thread_id"] == PA.thread_key("s1") != "s1"
    P.caller_principal.set(caller())
    assert orch._config("s1")["configurable"]["thread_id"] == "s1"


async def test_what_a_session_stored_before_the_cut_is_not_read_back(monkeypatch):
    token = meta_tools._session_ctx.set("s9")
    try:
        P.caller_principal.set(caller())
        await meta_tools.memory_set.ainvoke({"key": "finding", "value": "energy anomalies: 14"})
        assert "14" in await meta_tools.memory_get.ainvoke({"key": "finding"})
        P.caller_principal.set(caller(COMPLIANCE, CUT))
        assert "14" not in str(await meta_tools.memory_get.ainvoke({"key": "finding"}))
    finally:
        meta_tools._session_ctx.reset(token)


# ── the compliance pipeline's own reads ───────────────────────────────────────

async def test_open_work_orders_are_not_read_for_a_caller_without_the_maintenance_page():
    orch = orchestrator.DeepAgentOrchestrator.__new__(orchestrator.DeepAgentOrchestrator)
    read = []

    async def _open():
        read.append(1)
        return [{"code": "WO-1"}]
    orch._fetch_open_work_orders = _open
    P.caller_principal.set(caller(COMPLIANCE))
    assert await orch._fetch_planned_extras(["work_orders"]) == []
    assert read == []
    P.caller_principal.set(caller(frozenset({"compliance", "ops"})))
    out = await orch._fetch_planned_extras(["work_orders"])
    assert out[0]["output"] == {"work_orders": [{"code": "WO-1"}]}
    P.caller_principal.set(caller())
    assert len(await orch._fetch_planned_extras(["work_orders"])) == 1
    assert len(read) == 2


# ── a replay, and a cut that cannot be read ───────────────────────────────────

async def test_a_turn_from_before_the_cut_is_not_replayed(monkeypatch):
    async def _turn(_principal, _org, _turn_id):
        return {"turn_id": "t1", "started_at": (CUT - timedelta(days=2)).isoformat(), "question": "q"}
    monkeypatch.setattr(traces_routes.trace, "get_turn", _turn)
    body = traces_routes.RerunBody(session_id="s1", corrections=[])
    request = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as e:
        await traces_routes.rerun_turn("t1", body, request, None, principal=caller(COMPLIANCE, CUT),
                                       orchestrator=SimpleNamespace())
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:                 # everyone else gets as far as before
        await traces_routes.rerun_turn("t1", body, request, None, principal=caller(),
                                       orchestrator=SimpleNamespace())
    assert e.value.status_code == 422


def test_a_cut_that_cannot_be_read_hides_everything():
    me = {"user": {"id": str(uuid4()), "role": "user", "building_ids": [], "pages": ["energy"],
                   "access_narrowed_at": "yesterday"}}
    cut = P._from_me(me).access_narrowed_at
    assert cut is not None and cut > datetime(9000, 1, 1, tzinfo=timezone.utc)
    me["user"]["access_narrowed_at"] = None
    assert P._from_me(me).access_narrowed_at is None
    del me["user"]["pages"]                                  # not held to pages: nothing to hide
    me["user"]["access_narrowed_at"] = "yesterday"
    assert P._from_me(me).access_narrowed_at is None


def test_a_meter_readings_upload_is_the_energy_pages():
    assert PA.document_page("meter_readings") == "energy"
