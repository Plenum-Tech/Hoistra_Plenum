"""Trace Lab syncs the chat's new answers by itself (3 Nov 2026).

Since 13 Nov 2026 the same round also files the chat's conversations as sessions (eval_store, run tag "chat"), so a
question typed in the chat is in both places without anyone pressing a button: on Runs as a question of that person's
day, and on Sessions as its whole conversation, a trace per turn. TRACE_LAB_AUTO_SYNC_CHAT_SESSIONS=0 turns this part
off. Scenario sessions (scn__<tag>__<scenario>__<n>) are filed by the same round too (sync_scenario_sessions, Hussain
13 Nov 2026: "it all should be synced"); TRACE_LAB_AUTO_SYNC_SCENARIO_SESSIONS=0 leaves them to "Read new sessions".

And the other way round: a session whose turns this environment's chat never recorded (one copied here from another
Trace Lab) becomes questions in Runs, a run per run tag (runs.sync_sessions). TRACE_LAB_AUTO_SYNC_SESSION_RUNS=0
turns that part off.

Until now a run of live questions appeared only when someone pressed "Sync now". This loop does the same sync for every
company every TRACE_LAB_AUTO_SYNC_S seconds (default 120; 0 turns it off) once the schema is ready. It is the same
runs.sync_live the button runs: it only ever adds questions for answers it has not seen (one lock, the synced-turn
index), so older runs, notes, labels, expected answers and failure modes are never touched. A failed round is logged
and the next one tries again; nothing here can stop the service or raise into a request.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from . import db

log = structlog.get_logger(__name__)

#: Rounds of one tick: a backlog of more than this many batches is finished by the next tick.
MAX_BATCHES = 5
FIRST_DELAY_S = 20.0


def every_s() -> float:
    try:
        return max(0.0, float(os.environ.get("TRACE_LAB_AUTO_SYNC_S", "120")))
    except ValueError:
        return 120.0


#: How far back each round looks for scenario sessions to file (13 Nov 2026). The "Read new sessions" button looks
#: back 90 days for anything older.
SESSIONS_HOURS = 24

STATUS: dict[str, Any] = {"enabled": False, "every_s": 0, "last_run_at": None, "last_ok": None,
                          "last_new_questions": 0, "last_error": None, "next_at": None,
                          "sessions_ok": None, "last_new_sessions": 0, "sessions_error": None,
                          "session_runs_ok": None, "last_session_questions": 0, "session_runs_error": None,
                          "scenario_sessions_ok": None, "last_new_scenario_sessions": 0,
                          "scenario_sessions_error": None}


def session_runs_on() -> bool:
    return os.environ.get("TRACE_LAB_AUTO_SYNC_SESSION_RUNS", "1").strip().lower() not in ("0", "false", "no", "off")


async def sync_session_runs(now: datetime | None = None) -> int:
    """Sessions -> Runs, for every company, until nothing is left or MAX_BATCHES ran. Its own try, as above."""
    if not session_runs_on():
        return 0
    from . import runs

    total = 0
    try:
        for _ in range(MAX_BATCHES):
            out = await runs.sync_sessions(None, now=now)
            total += int(out.get("new_questions") or 0)
            if not out.get("more"):
                break
        STATUS.update(session_runs_ok=True, last_session_questions=total, session_runs_error=None)
        if total:
            log.info("trace_lab.autosync.session_runs", new_questions=total)
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        STATUS.update(session_runs_ok=False, session_runs_error=str(exc)[:300])
        log.warning("trace_lab.autosync.session_runs_failed", error=str(exc)[:300])
    return total


def chat_sessions_on() -> bool:
    return os.environ.get("TRACE_LAB_AUTO_SYNC_CHAT_SESSIONS", "1").strip().lower() not in ("0", "false", "no", "off")


async def sync_chat_sessions(now: datetime | None = None) -> int:
    """File the chat's conversations of the last week as sessions, for every company. Its own try: a failure here
    is logged and never marks the questions' sync as failed, nor the other way round."""
    if not chat_sessions_on():
        return 0
    from . import eval_store

    started = now or datetime.now(timezone.utc)
    try:
        out = await eval_store.ingest_from_main(since=started - timedelta(days=eval_store.CHAT_SYNC_DAYS),
                                                chat=True, scenarios=False)
        new = int(out.get("sessions") or 0)
        STATUS.update(sessions_ok=True, last_new_sessions=new, sessions_error=None)
        if new or int(out.get("traces") or 0) or int(out.get("failed") or 0):
            log.info("trace_lab.autosync.chat_sessions", new_sessions=new, new_traces=int(out.get("traces") or 0),
                     failed=int(out.get("failed") or 0), other_company=int(out.get("other_company") or 0))
        return new
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        STATUS.update(sessions_ok=False, sessions_error=str(exc)[:300])
        log.warning("trace_lab.autosync.chat_sessions_failed", error=str(exc)[:300])
        return 0


def scenario_sessions_on() -> bool:
    return os.environ.get("TRACE_LAB_AUTO_SYNC_SCENARIO_SESSIONS", "1").strip().lower() not in ("0", "false", "no", "off")


async def sync_scenario_sessions(now: datetime | None = None) -> int:
    """File every company's new scenario sessions (scn__<tag>__<scenario>__<n>) of the last SESSIONS_HOURS, the way
    "Read new sessions" does (13 Nov 2026, Hussain: "it all should be synced"). A turn that arrives after its session
    was filed is added to it by a later round (eval_store.write_session appends it as the next turn), so a scenario
    still being asked is safe to file. TRACE_LAB_AUTO_SYNC_SCENARIO_SESSIONS=0 turns it off. Its own try, as above."""
    if not scenario_sessions_on():
        STATUS.update(scenario_sessions_ok=None, last_new_scenario_sessions=0, scenario_sessions_error=None)
        return 0
    from . import eval_store

    started = now or datetime.now(timezone.utc)
    try:
        out = await eval_store.ingest_from_main(since=started - timedelta(hours=SESSIONS_HOURS),
                                                chat=False, scenarios=True)
        new = int(out.get("sessions") or 0)
        STATUS.update(scenario_sessions_ok=True, last_new_scenario_sessions=new, scenario_sessions_error=None)
        if new or int(out.get("traces") or 0) or int(out.get("failed") or 0):
            log.info("trace_lab.autosync.scenario_sessions", new_sessions=new, new_traces=int(out.get("traces") or 0),
                     failed=int(out.get("failed") or 0), waiting=int(out.get("waiting") or 0))
        return new
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        STATUS.update(scenario_sessions_ok=False, last_new_scenario_sessions=0, scenario_sessions_error=str(exc)[:300])
        log.warning("trace_lab.autosync.scenario_sessions_failed", error=str(exc)[:300])
        return 0
_task: asyncio.Task | None = None


async def tick(now: datetime | None = None) -> dict[str, Any]:
    """One round: sync every company until nothing is left or MAX_BATCHES ran."""
    from . import runs

    total = 0
    started = now or datetime.now(timezone.utc)
    try:
        for _ in range(MAX_BATCHES):
            out = await runs.sync_live(None)
            total += int(out.get("new_questions") or 0)
            if not out.get("more"):
                break
        STATUS.update(last_run_at=started.isoformat(), last_ok=True, last_new_questions=total, last_error=None)
        if total:
            log.info("trace_lab.autosync.synced", new_questions=total)
    except Exception as exc:  # noqa: BLE001 - the next round tries again
        STATUS.update(last_run_at=started.isoformat(), last_ok=False, last_error=str(exc)[:300])
        log.warning("trace_lab.autosync.failed", error=str(exc)[:300])
    await sync_chat_sessions(now)
    await sync_scenario_sessions(now)
    await sync_session_runs(now)
    return dict(STATUS)


async def _loop(every: float) -> None:
    await asyncio.sleep(min(FIRST_DELAY_S, every))
    while True:
        if db.STATE is not None and getattr(db.STATE, "status", None) == "ready":
            await tick()
        STATUS["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=every)).isoformat()
        await asyncio.sleep(every)


def start() -> bool:
    """Start the loop (once) when TRACE_LAB_AUTO_SYNC_S is above 0. Called from startup, never raises."""
    global _task
    every = every_s()
    STATUS.update(enabled=every > 0, every_s=int(every))
    if every <= 0 or (_task is not None and not _task.done()):
        return False
    try:
        _task = asyncio.get_running_loop().create_task(_loop(every), name="trace-lab-autosync")
        STATUS["next_at"] = (datetime.now(timezone.utc) + timedelta(seconds=min(FIRST_DELAY_S, every))).isoformat()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("trace_lab.autosync.start_failed", error=str(exc)[:200])
        return False


async def stop() -> None:
    global _task
    t, _task = _task, None
    if t is not None and not t.done():
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


def status() -> dict[str, Any]:
    return dict(STATUS)
