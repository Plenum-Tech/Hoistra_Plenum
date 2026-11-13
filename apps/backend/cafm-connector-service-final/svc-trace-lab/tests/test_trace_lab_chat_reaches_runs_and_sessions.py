"""A question typed in the chat reaches both Runs and Sessions by itself (13 Nov 2026).

Runs has synced the chat's answers every couple of minutes since 3 Nov (autosync -> runs.sync_live). Sessions read
scenario sessions only, and only when a curator pressed "Read new sessions", so the same question was a row on Runs
and nowhere on Sessions - "we want to sync both session and run so that it can be used in both".

The same round now files the chat's conversations as sessions too (run tag "chat"). Scenario sessions stay with the
button: a scenario still being asked must not be filed, because a session row means its scenario is finished.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from trace_lab import autosync, eval_store, runs


@pytest.fixture
def synced(monkeypatch):
    """The two reads of one round, recorded instead of run."""
    calls = {"runs": [], "sessions": []}
    replies = {"runs": {"new_questions": 2, "new_runs": 1, "more": False},
               "sessions": {"sessions": 1, "traces": 2, "observations": 9, "failed": 0, "other_company": 0}}

    async def sync_live(org, **kw):
        calls["runs"].append(org)
        if isinstance(replies["runs"], Exception):
            raise replies["runs"]
        return replies["runs"]

    async def ingest_from_main(**kw):
        calls["sessions"].append(kw)
        if isinstance(replies["sessions"], Exception):
            raise replies["sessions"]
        return replies["sessions"]

    monkeypatch.setattr(runs, "sync_live", sync_live)
    monkeypatch.setattr(eval_store, "ingest_from_main", ingest_from_main)
    monkeypatch.delenv("TRACE_LAB_AUTO_SYNC_CHAT_SESSIONS", raising=False)
    monkeypatch.delenv("TRACE_LAB_AUTO_SYNC_SCENARIO_SESSIONS", raising=False)
    autosync.STATUS.update(last_ok=None, last_error=None, last_new_questions=0, sessions_ok=None,
                           last_new_sessions=0, sessions_error=None, scenario_sessions_ok=None,
                           last_new_scenario_sessions=0, scenario_sessions_error=None)
    return calls, replies


def test_one_round_syncs_the_chat_to_runs_and_to_sessions(synced):
    calls, _ = synced
    now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    status = asyncio.run(autosync.tick(now))
    assert calls["runs"] == [None]                                   # every company
    kw = calls["sessions"][0]
    assert kw["chat"] is True and kw["scenarios"] is False
    assert kw["since"] == now - timedelta(days=eval_store.CHAT_SYNC_DAYS)
    assert "org" not in kw and "sessions" not in kw                  # every company, found by their ids
    assert status["last_ok"] is True and status["last_new_questions"] == 2
    assert status["sessions_ok"] is True and status["last_new_sessions"] == 1


def test_scenario_sessions_are_filed_by_the_round_too_and_can_be_left_to_the_button(synced, monkeypatch):
    """Hussain, 13 Nov 2026 ("it all should be synced"): the round files scenario sessions as well, in a read of its
    own; a turn that arrives later is added to its session by a later round. One setting leaves them to the person
    who presses Read new sessions, as before."""
    calls, _ = synced
    now = datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
    status = asyncio.run(autosync.tick(now))
    scenario = [kw for kw in calls["sessions"] if kw["scenarios"]]
    assert len(scenario) == 1 and scenario[0]["chat"] is False
    assert scenario[0]["since"] == now - timedelta(hours=autosync.SESSIONS_HOURS)
    assert status["scenario_sessions_ok"] is True and status["last_new_scenario_sessions"] == 1
    calls["sessions"].clear()
    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_SCENARIO_SESSIONS", "0")
    status = asyncio.run(autosync.tick(now))
    assert all(kw["scenarios"] is False for kw in calls["sessions"]), "the chat read alone"
    assert status["scenario_sessions_ok"] is None and status["last_new_scenario_sessions"] == 0


def test_a_failure_filing_sessions_does_not_fail_the_questions_sync(synced):
    calls, replies = synced
    replies["sessions"] = RuntimeError("the lab database is busy")
    status = asyncio.run(autosync.tick())
    assert status["last_ok"] is True and status["last_new_questions"] == 2
    assert status["sessions_ok"] is False and "busy" in status["sessions_error"]


def test_a_failure_syncing_questions_does_not_stop_the_sessions(synced):
    calls, replies = synced
    replies["runs"] = RuntimeError("the chat database did not answer")
    status = asyncio.run(autosync.tick())
    assert status["last_ok"] is False
    assert len(calls["sessions"]) == 2 and status["sessions_ok"] is True, "the chat read and the scenario read"
    assert status["scenario_sessions_ok"] is True


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_the_sessions_part_can_be_turned_off(synced, monkeypatch, value):
    calls, _ = synced
    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_CHAT_SESSIONS", value)
    asyncio.run(autosync.tick())
    assert calls["runs"] == [None] and [kw for kw in calls["sessions"] if kw["chat"]] == [], "no chat read"


def test_the_button_and_the_round_look_back_the_same_week():
    from trace_lab import routes_eval

    assert routes_eval.CHAT_INGEST_DAYS == eval_store.CHAT_SYNC_DAYS == 7


def test_reading_neither_kind_reads_nothing():
    """ingest_from_main(scenarios=False) without chat has nothing to look for and touches no database."""
    import inspect

    body = inspect.getsource(eval_store.ingest_from_main)
    assert "if not kinds:" in body and body.index("if not kinds:") < body.index("found = [dict(r)")


# ── and the other way round: a session with no chat record here reaches Runs ──────────────────────────

@pytest.fixture
def sessions_to_runs(synced, monkeypatch):
    calls, replies = synced
    calls["session_runs"] = []
    replies["session_runs"] = {"new_questions": 3, "new_runs": 1, "more": False}

    async def sync_sessions(org, **kw):
        calls["session_runs"].append((org, kw))
        if isinstance(replies["session_runs"], Exception):
            raise replies["session_runs"]
        return replies["session_runs"]

    monkeypatch.setattr(runs, "sync_sessions", sync_sessions)
    monkeypatch.delenv("TRACE_LAB_AUTO_SYNC_SESSION_RUNS", raising=False)
    autosync.STATUS.update(session_runs_ok=None, last_session_questions=0, session_runs_error=None)
    return calls, replies


def test_the_round_also_makes_questions_of_sessions_the_chat_here_never_recorded(sessions_to_runs):
    """Thirty sessions pushed from another machine (frontend_nov13_202718) were in Sessions and not in Runs."""
    calls, _ = sessions_to_runs
    status = asyncio.run(autosync.tick())
    assert [org for org, _kw in calls["session_runs"]] == [None]            # every company
    assert status["session_runs_ok"] is True and status["last_session_questions"] == 3
    # after the chat's own sync, so a turn the chat recorded is already a question of its person's day
    assert calls["runs"] == [None] and len(calls["sessions"]) == 2


def test_a_backlog_is_worked_through_in_batches(sessions_to_runs):
    calls, replies = sessions_to_runs
    replies["session_runs"] = {"new_questions": 2000, "new_runs": 0, "more": True}
    asyncio.run(autosync.tick())
    assert len(calls["session_runs"]) == autosync.MAX_BATCHES


def test_a_failure_there_fails_neither_of_the_other_two(sessions_to_runs):
    calls, replies = sessions_to_runs
    replies["session_runs"] = RuntimeError("the lab database is busy")
    status = asyncio.run(autosync.tick())
    assert status["last_ok"] is True and status["sessions_ok"] is True
    assert status["session_runs_ok"] is False and "busy" in status["session_runs_error"]


def test_that_part_can_be_turned_off_too(sessions_to_runs, monkeypatch):
    calls, _ = sessions_to_runs
    monkeypatch.setenv("TRACE_LAB_AUTO_SYNC_SESSION_RUNS", "0")
    asyncio.run(autosync.tick())
    assert calls["session_runs"] == [] and calls["runs"] == [None]
