"""An approval or an approver email is never sent for another session's work order (29 Oct 2026).

request_approval_chain and send_approval_request_email fell back to _LAST_WORK_ORDER_ID — the last
work order ANY session in the process touched — when the asking session had none of its own; the
orchestrator's short-reply path ("yes", "send it") preferred that process-wide id even over the one
the user typed. So a "yes" in one company's chat could request approval, or email an approver, for
another company's work order. A session's own work order, or the id it names, or nothing.
"""
import asyncio

from src.agents import wo_engine_agent as wea

OTHER = "WO-20261029000001"


def _isolate(monkeypatch, calls):
    monkeypatch.setattr(wea, "_LAST_WORK_ORDER_ID", OTHER)
    monkeypatch.setattr(wea, "_LAST_WO_CHAT_SESSION_ID", "someone-elses-chat")
    monkeypatch.setattr(wea, "_SESSION_WORK_ORDER_MAP", {"someone-elses-chat": OTHER})

    async def _request(method, base, path, **kw):
        calls.append(path)
        raise AssertionError("no request may be sent: " + path)

    monkeypatch.setattr(wea, "_request", _request)
    monkeypatch.setattr(wea, "_orch_session_id", lambda: "my-session")


def test_an_approval_chain_is_not_requested_for_another_sessions_work_order(monkeypatch):
    calls = []
    _isolate(monkeypatch, calls)
    out = asyncio.run(wea.request_approval_chain.ainvoke({"session_id": "my-session"}))
    assert calls == [] and "error" in out


def test_an_approver_email_is_not_sent_for_another_sessions_work_order(monkeypatch):
    calls = []
    _isolate(monkeypatch, calls)
    out = asyncio.run(wea.send_approval_request_email.ainvoke({"session_id": "my-session"}))
    assert calls == [] and "error" in out


def test_the_short_reply_path_never_reads_the_process_wide_work_order():
    import inspect

    from src.agents.orchestrator import DeepAgentOrchestrator as O

    src = inspect.getsource(O._maybe_short_reply_wo_action)
    assert "_LAST_WORK_ORDER_ID" not in src and "_GLOBAL_WO_ID" not in src
