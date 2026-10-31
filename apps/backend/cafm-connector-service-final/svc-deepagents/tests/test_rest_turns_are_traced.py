"""Every REST turn is a trace, whichever path answers it; a turn closed twice flushes once.

Found 4 Oct 2026: the day's Hoist Crons sessions had 82 activity rows and no trace turn - the
turn was opened only inside _invoke, and the compliance shortcut returned before it."""
from __future__ import annotations

import asyncio

from src.agents import activity_log, trace
from src.agents.orchestrator import DeepAgentOrchestrator as O


def test_a_second_turn_output_does_not_flush_twice(monkeypatch):
    written = []

    async def fake_write(t, answer, tool_calls, summary):
        written.append(answer)

    monkeypatch.setattr(trace, "_ready", True)
    monkeypatch.setattr(trace, "_write", fake_write)

    async def run():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-r", "session_id": "report-1",
                           "summary": "which certificates expired?", "payload": {"message": "which certificates expired?", "mode": "rest"}})
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "9 lapsed", "payload": {"answer": "9 lapsed", "tool_calls": []}})
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "output", "summary": "9 lapsed", "payload": {"answer": "9 lapsed", "tool_calls": []}})
        await asyncio.sleep(0)

    asyncio.run(run())
    assert written == ["9 lapsed"]


def test_run_stateful_opens_and_closes_the_turn_around_any_answer_path(monkeypatch):
    fired = []
    monkeypatch.setattr(activity_log, "fire", lambda **kw: fired.append((kw.get("stage"), kw.get("direction"), kw.get("summary"), (kw.get("payload") or {}).get("mode"))))
    monkeypatch.setattr(activity_log, "set_current_session", lambda *a, **k: None)
    monkeypatch.setattr(activity_log, "ensure_turn", lambda *a, **k: None)
    o = O.__new__(O)

    async def inner(**kw):
        # a shortcut answer: never reaches _invoke, so nothing else would have closed the turn
        return {"answer": "There are 9 lapsed certificates.", "tool_calls": [{"tool": "count_compliance_certificates"}],
                "success": True, "route_metadata": {"intent": "compliance"}}

    o._run_stateful_inner = inner
    out = asyncio.run(O.run_stateful(o, user_message="which certificates expired?", session_id="report-x"))
    assert out["answer"].startswith("There are 9")
    assert fired[0][:2] == ("turn", "input") and fired[0][3] == "rest" and fired[0][2] == "which certificates expired?"
    assert fired[-1][:2] == ("turn", "output") and fired[-1][2].startswith("There are 9")

    async def boom(**kw):
        raise RuntimeError("engine down")

    o._run_stateful_inner = boom
    fired.clear()
    try:
        asyncio.run(O.run_stateful(o, user_message="q", session_id="report-y"))
    except RuntimeError:
        pass
    assert [f[:2] for f in fired] == [("turn", "input"), ("turn", "error")]
