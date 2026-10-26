"""Trace Lab's own chat orchestrator, and the checks that keep golden runs from touching anything (G0
addendum 2.5).

Golden runs ask the real chat - svc-deepagents' DeepAgentOrchestrator, imported, never edited - inside
this process. Safety comes from the process, not from a replay flag:

* the orchestrator is Trace Lab's own, built lazily with an in-memory checkpointer (MemorySaver), so
  follow-ups keep their context and no checkpoint row is ever written;
* no existing store is initialised here (trace, chat threads, memories, skill lab keep _ready False),
  so nothing is recorded, recalled, learned or forgotten;
* activity logging is off, the raw-httpx writers point at a dead port, every service URL points at
  Trace Lab's allow-list proxy, and src.database runs on Trace Lab's read-only engine.

assert_isolated() checks all of that before a run starts. Approved skill overlays are loaded read-only
(ensure_overlays_readonly): skill_overlays._ready is pre-set so its refresh never runs CREATE TABLE, and
the active rows are read with one SELECT. These private touch points are pinned by Trace Lab's tests.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Any

import structlog
from sqlalchemy import text

from . import db

log = structlog.get_logger(__name__)

PROXY_BASE = "http://127.0.0.1:8010/__proxy"
DEAD_MIGRATION_URL = "http://127.0.0.1:9"
#: svc-deepagents modules that copy settings.migration_base_url into a module-level _BASE at import time
#: (migration_agent.py:39, fiix_agent.py:47, schema_mapper_agent.py:23): checked too, when imported.
WRITER_MODULES = ("src.agents.migration_agent", "src.agents.fiix_agent", "src.agents.schema_mapper_agent")
#: svc-deepagents' settings attribute -> the proxy service it must point at (its env var is the upper case).
BASE_URL_SETTINGS: dict[str, str] = {
    "udr_base_url": "udr",
    "operations_intelligence_base_url": "oi",
    "wo_management_base_url": "wom",
    "doc_rag_base_url": "docrag",
}
#: A golden conversation's session id (runner.session_id_for): only these are ever released.
GOLDEN_SESSION = re.compile(r"^golden-[0-9a-f]{12}-[A-Za-z0-9][A-Za-z0-9._-]{0,63}-r\d{1,2}(?:a\d)?$")

_orchestrator: Any = None
_overlays_loaded = False


def get_orchestrator() -> Any:
    """The process's one golden-run orchestrator, built on first use from svc-deepagents' settings with
    an in-memory checkpointer. Building it writes nothing and calls no service."""
    global _orchestrator
    if _orchestrator is None:
        from langgraph.checkpoint.memory import MemorySaver
        from src.agents.orchestrator import DeepAgentOrchestrator
        from src.config import settings

        _orchestrator = DeepAgentOrchestrator(openai_api_key=settings.openai_api_key, model=settings.openai_model,
                                              checkpointer=MemorySaver())
        log.info("trace_lab.orchestrator.ready", model=settings.openai_model, checkpointer="memory")
    return _orchestrator


def _checkpointer(orchestrator: Any) -> Any:
    return getattr(getattr(orchestrator, "_agent", None), "checkpointer", None)


async def ensure_overlays_readonly() -> int:
    """Approved skill overlays, loaded without DDL: pre-set skill_overlays._ready (so its refresh() never
    runs CREATE TABLE and later refreshes are plain SELECTs on the read-only engine), then read the active
    rows with one SELECT. Returns how many are active; a missing table reads as none."""
    global _overlays_loaded
    from src.services import skill_overlays

    skill_overlays._ready = True
    rows: list[Any] = []
    try:
        async with db.main_engine().begin() as conn:
            await conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            rows = list((await conn.execute(text(
                f"SELECT skill, doc, content FROM {skill_overlays.TABLE} WHERE status = 'active'"))).all())
    except Exception as exc:  # noqa: BLE001 - no overlays is a fidelity caveat, never a failed run
        log.warning("trace_lab.overlays.unreadable", error=str(exc)[:200])
    skill_overlays._active = {(str(r[0]), str(r[1])): str(r[2]) for r in rows}
    skill_overlays._loaded_at = time.monotonic()
    _overlays_loaded = True
    return len(skill_overlays._active)


def overlays_loaded() -> bool:
    return _overlays_loaded


def assert_isolated() -> list[str]:
    """Why this process may NOT run a golden case right now (empty = isolated)."""
    from src import database
    from src.agents import activity_log, skill_lab, trace
    from src.config import settings
    from src.services import chat_memories, chat_threads

    problems: list[str] = []
    for name, module in (("trace", trace), ("chat thread", chat_threads), ("memory", chat_memories),
                         ("skill lab", skill_lab)):
        if getattr(module, "_ready", False):
            problems.append(f"The {name} store is initialised in this process; Trace Lab never initialises it.")
    if activity_log.enabled():
        problems.append("Activity logging is on: ACTIVITY_LOG_ENABLED must be false for Trace Lab.")
    if (settings.migration_base_url or "").rstrip("/") != DEAD_MIGRATION_URL:
        problems.append(f"MIGRATION_BASE_URL must be {DEAD_MIGRATION_URL}, so the direct writers reach nothing.")
    for name in WRITER_MODULES:              # these copy settings.migration_base_url into _BASE at import
        module = sys.modules.get(name)
        if module is not None and (str(getattr(module, "_BASE", "") or "")).rstrip("/") != DEAD_MIGRATION_URL:
            problems.append(f"{name.rsplit('.', 1)[1]} kept a live writer address from start-up; restart Trace Lab "
                            f"with MIGRATION_BASE_URL={DEAD_MIGRATION_URL}.")
    for attr, key in BASE_URL_SETTINGS.items():
        want = f"{PROXY_BASE}/{key}"
        if (getattr(settings, attr, "") or "").rstrip("/") != want:
            problems.append(f"{attr.upper()} must be {want}, Trace Lab's allow-list proxy.")
    try:
        main = db.main_engine()
    except RuntimeError:
        problems.append("The read-only main engine is not installed.")
    else:
        if database._engine is not main:
            problems.append("svc-deepagents' database module is not using Trace Lab's read-only engine.")
    if _orchestrator is not None:
        from langgraph.checkpoint.memory import MemorySaver

        if not isinstance(_checkpointer(_orchestrator), MemorySaver):
            problems.append("The orchestrator was not built with an in-memory checkpointer.")
    return problems


def release_session(session_id: str) -> None:
    """Forget a finished golden conversation's in-process state: its session workspace entry, the
    sub-agent scratch memory, its in-memory checkpoint thread and its temp folder. Only golden session ids
    are touched; anything else is left alone."""
    sid = str(session_id or "")
    if not GOLDEN_SESSION.match(sid):
        log.warning("trace_lab.release_session.refused", session_id=sid[:120])
        return
    from src.agents import meta_tools, session_workspace

    session_workspace._SESSION_STATE.pop(sid, None)
    meta_tools._MEMORY.pop(sid, None)
    saver = _checkpointer(_orchestrator) if _orchestrator is not None else None
    if saver is not None and hasattr(saver, "delete_thread"):
        try:
            saver.delete_thread(sid)
        except Exception as exc:  # noqa: BLE001 - memory only; the next process starts empty anyway
            log.warning("trace_lab.release_session.thread", error=str(exc)[:200])
    root = os.path.abspath(meta_tools._TEMP_DIR)
    folder = os.path.abspath(os.path.join(root, sid))
    if os.path.dirname(folder) == root:
        shutil.rmtree(folder, ignore_errors=True)


def reset_for_tests() -> None:
    global _orchestrator, _overlays_loaded
    _orchestrator = None
    _overlays_loaded = False
