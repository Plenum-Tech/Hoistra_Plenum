"""Chat threads — every conversation with the orchestrator, kept on the server (memory phase A).

Until 2 Oct 2026 the only lasting record of a conversation was the LangGraph checkpoint under its
session_id. The "recent conversation" block injected into each turn, the workspace facts and the
list of sessions all lived in process memory or in the browser's localStorage: a restart, a second
replica or another device lost them. This module is the durable, company-scoped record:

    plenum_cafm.chat_threads   one row per session: who, which company, title, running summary
    plenum_cafm.chat_turns     one row per exchange: the question, the answer, the tools behind it

and the two things built from it:

* `conversation_context(session_id)` - what the model is told about the thread so far: the
  stored summary of the older turns plus the last few verbatim. It replaces the process-local
  block, so a reopened thread continues after a restart, on any replica.
* `fold_summary(session_id)` - once enough turns have fallen out of the verbatim window, a small
  model pass folds them into the running summary. Scheduled after an answer, never awaited by it.

Rules, the same as activity_log's: nothing here ever breaks a turn (every write is best-effort and
logged on failure); the caller comes from the request's ContextVars (principal, acting company);
reads are scoped to the caller's company and - for a thread - its owner. Later phases add the
memories extracted from these turns; the turns stay the source they point back to.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from ..http_client import caller_organization_id
from .principal import Principal, caller_principal

log = structlog.get_logger(__name__)

THREADS = "plenum_cafm.chat_threads"
TURNS = "plenum_cafm.chat_turns"

#: Turns repeated verbatim in the context; older ones are read through the summary.
CONTEXT_TURNS = 10
#: Each side of a turn is cut to this in the context (the row keeps the whole text).
CONTEXT_CHARS = 4000
#: Fold the summary once this many turns sit beyond the verbatim window and outside it.
FOLD_AFTER = 6
SUMMARY_MAX_CHARS = 1800
TITLE_CHARS = 120

_ready = False
_folding: set[str] = set()

_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {THREADS} (
        id               TEXT PRIMARY KEY,
        organization_id  UUID,
        user_id          UUID NOT NULL,
        email            TEXT,
        title            TEXT,
        building_id      UUID,
        summary          TEXT,
        summary_through  INTEGER NOT NULL DEFAULT 0,
        turn_count       INTEGER NOT NULL DEFAULT 0,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_message_at  TIMESTAMPTZ,
        deleted_at       TIMESTAMPTZ
    )""",
    f"""
    CREATE TABLE IF NOT EXISTS {TURNS} (
        id               UUID PRIMARY KEY,
        thread_id        TEXT NOT NULL,
        turn_no          INTEGER NOT NULL,
        organization_id  UUID,
        user_id          UUID,
        question         TEXT,
        answer           TEXT,
        tools            JSONB,
        citations        JSONB,
        route            TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        answered_at      TIMESTAMPTZ,
        UNIQUE (thread_id, turn_no)
    )""",
    f"ALTER TABLE {THREADS} ADD COLUMN IF NOT EXISTS working_set JSONB",
    f"CREATE INDEX IF NOT EXISTS ix_chat_threads_owner ON {THREADS} (user_id, organization_id, last_message_at DESC)",
    f"CREATE INDEX IF NOT EXISTS ix_chat_turns_thread ON {TURNS} (thread_id, turn_no)",
]


def ready() -> bool:
    return _ready


async def ensure_tables() -> bool:
    """Create the tables if missing. Returns True when the store is usable."""
    global _ready
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            await conn.execute(text("CREATE SCHEMA IF NOT EXISTS plenum_cafm"))
            for stmt in _DDL:
                await conn.execute(text(stmt))
        _ready = True
        log.info("chat_threads.ready")
    except Exception as exc:  # noqa: BLE001
        _ready = False
        log.warning("chat_threads.tables_failed", error=str(exc)[:300])
    return _ready


def _caller() -> tuple[Principal | None, str | None]:
    """The signed-in caller and the company they are acting for (a superadmin viewing as one)."""
    p = caller_principal.get()
    org = caller_organization_id.get() or (str(p.organization_id) if p and p.organization_id else None)
    return p, org


def _title(question: str) -> str:
    t = " ".join((question or "").split())
    return t if len(t) <= TITLE_CHARS else t[:TITLE_CHARS - 1] + "…"


# ── writing ───────────────────────────────────────────────────────────────────────────

async def record_question(session_id: str, question: str, *, building_id: str | None = None,
                          route: str | None = None) -> None:
    """The user's side of a turn: creates the thread on its first question."""
    if not _ready or not session_id or not (question or "").strip():
        return
    p, org = _caller()
    if p is None:
        return
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            await conn.execute(text(f"""
                INSERT INTO {THREADS} (id, organization_id, user_id, email, title, building_id, last_message_at)
                VALUES (:id, CAST(:org AS uuid), CAST(:uid AS uuid), :email, :title, CAST(:bid AS uuid), now())
                ON CONFLICT (id) DO UPDATE
                   SET last_message_at = now(), updated_at = now(), deleted_at = NULL,
                       title = COALESCE({THREADS}.title, EXCLUDED.title),
                       building_id = COALESCE(EXCLUDED.building_id, {THREADS}.building_id)"""),
                {"id": session_id, "org": org, "uid": str(p.user_id), "email": p.email,
                 "title": _title(question), "bid": building_id})
            await conn.execute(text(f"""
                INSERT INTO {TURNS} (id, thread_id, turn_no, organization_id, user_id, question, route)
                SELECT CAST(:id AS uuid), :tid, COALESCE(max(turn_no), 0) + 1, CAST(:org AS uuid), CAST(:uid AS uuid),
                       :q, :route
                  FROM {TURNS} WHERE thread_id = :tid"""),
                {"id": str(uuid.uuid4()), "tid": session_id, "org": org, "uid": str(p.user_id),
                 "q": question, "route": route})
            await conn.execute(text(f"UPDATE {THREADS} SET turn_count = (SELECT count(*) FROM {TURNS} WHERE thread_id = :tid)"
                                    " WHERE id = :tid"), {"tid": session_id})
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_threads.record_question_failed", session_id=session_id, error=str(exc)[:300])


async def record_answer(session_id: str, answer: str, *, tools: list | None = None,
                        citations: list | None = None) -> None:
    """The assistant's side: fills the thread's NEWEST turn if it is unanswered, then schedules a fold.

    Only the newest: a turn is answered by the answer that follows it. Some paths record one
    answer twice (attach_route_to_result, then the closing write); when the newest turn already
    had it, the second write used to fill an OLDER unanswered turn (a stopped or gated one),
    putting this answer under a different question (found 5 Oct 2026). Now it is a no-op."""
    if not _ready or not session_id or not (answer or "").strip():
        return
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            row = (await conn.execute(text(f"""
                UPDATE {TURNS} SET answer = :a, tools = CAST(:tools AS jsonb), citations = CAST(:cites AS jsonb),
                       answered_at = now()
                 WHERE id = (SELECT id FROM {TURNS} WHERE thread_id = :tid
                             ORDER BY turn_no DESC LIMIT 1)
                   AND answer IS NULL
             RETURNING question"""),
                {"a": answer, "tools": json.dumps(_tool_names(tools) + _card_payloads(tools), default=str), "cites": json.dumps(citations or []),
                 "tid": session_id})).first()
            if row is None:
                return          # the newest turn already has its answer (a repeat write): nothing to do
            head = (await conn.execute(text(f"SELECT working_set FROM {THREADS} WHERE id = :tid"),
                                       {"tid": session_id})).first()
            # What the thread is now about (agents/thread_scope.py): the next follow-up's hard filter.
            from ..agents import thread_scope
            previous = head[0] if head else None
            if isinstance(previous, str):
                previous = json.loads(previous)
            ws = thread_scope.derive(previous, row[0] if row else "", answer, tools)
            await conn.execute(text(f"UPDATE {THREADS} SET last_message_at = now(), updated_at = now(),"
                                    f" working_set = CAST(:ws AS jsonb) WHERE id = :tid"),
                               {"tid": session_id, "ws": json.dumps(ws) if ws else None})
            question = row[0] if row else ""
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_threads.record_answer_failed", session_id=session_id, error=str(exc)[:300])
        return
    schedule_fold(session_id)
    # What, if anything, this exchange taught us for later conversations (services/chat_memories.py).
    from . import chat_memories
    chat_memories.learn_soon(session_id, question, answer)


async def working_set(session_id: str) -> dict | None:
    """The thread's working set, or None when there is none or the store is unavailable."""
    if not _ready or not session_id:
        return None
    try:
        from ..database import _get_engine

        async with _get_engine().connect() as conn:
            row = (await conn.execute(text(f"SELECT working_set FROM {THREADS} WHERE id = :tid"),
                                      {"tid": session_id})).first()
        ws = row[0] if row else None
        return json.loads(ws) if isinstance(ws, str) else ws
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_threads.working_set_failed", session_id=session_id, error=str(exc)[:300])
        return None


def record_answer_soon(session_id: str, answer: str, *, tools: list | None = None) -> None:
    """record_answer from synchronous code: scheduled on the running loop, never awaited."""
    try:
        asyncio.get_running_loop().create_task(record_answer(session_id, answer, tools=tools))
    except RuntimeError:
        pass


# The chat renders an answer as the compliance dashboard from these two tool outputs. They are
# presentation, built from the answer's own figures, and are kept after the tool names so a chat
# reopened from the server renders as it did live (5 Oct 2026); every other tool's input and
# output is still dropped. Names stay strings, so a reader that wants names is unchanged.
CARD_TOOLS = ("compliance_pipeline", "compliance_response")
CARD_MAX_BYTES = 256_000


def _card_payloads(tools: list | None) -> list[dict]:
    # The FIRST of each: the chat draws the first compliance_response / compliance_pipeline it
    # finds (complianceLive.extractComplianceAnswer), so the reopened chat shows the same one.
    first: dict[str, dict] = {}
    for t in tools or []:
        if isinstance(t, dict) and t.get("tool") in CARD_TOOLS and isinstance(t.get("output"), dict):
            first.setdefault(t["tool"], t["output"])
    out = [{"tool": name, "output": first[name]} for name in CARD_TOOLS if name in first]
    if not out or len(json.dumps(out, default=str)) > CARD_MAX_BYTES:
        return []
    return out


def _tool_names(tools: list | None) -> list[str]:
    out: list[str] = []
    for t in tools or []:
        name = t.get("tool") or t.get("name") if isinstance(t, dict) else str(t)
        if name and name not in out:
            out.append(str(name)[:80])
    return out[:40]


# ── what the model is told ────────────────────────────────────────────────────────────

def _cut(s: str | None, n: int = CONTEXT_CHARS) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[:n] + "…"


def format_context(summary: str | None, turns: list[dict[str, Any]]) -> str:
    """The thread so far, as the model reads it: the summary of what fell out of the window,
    then the last turns verbatim. Empty when there is nothing."""
    if not summary and not turns:
        return ""
    lines = ["## Recent conversation (continue this thread; do not ask for context already given)"]
    if summary:
        lines.append("**Earlier in this conversation (summary):** " + _cut(summary, SUMMARY_MAX_CHARS))
    for t in turns:
        if t.get("question"):
            lines.append(f"**User:** {_cut(t['question'])}")
        if t.get("answer"):
            lines.append(f"**Assistant:** {_cut(t['answer'])}")
    return "\n".join(lines)


async def conversation_context(session_id: str) -> str | None:
    """None when the store is unavailable (the caller falls back to its process-local block)."""
    if not _ready or not session_id:
        return None
    try:
        from ..database import _get_engine

        async with _get_engine().connect() as conn:
            head = (await conn.execute(text(f"SELECT summary, summary_through FROM {THREADS} WHERE id = :tid"),
                                       {"tid": session_id})).mappings().first()
            if head is None:
                return ""
            rows = (await conn.execute(text(f"""
                SELECT question, answer, turn_no FROM {TURNS}
                 WHERE thread_id = :tid AND turn_no > :after AND answer IS NOT NULL
                 ORDER BY turn_no DESC LIMIT :n"""),
                {"tid": session_id, "after": int(head["summary_through"] or 0), "n": CONTEXT_TURNS})).mappings().all()
        return format_context(head["summary"], [dict(r) for r in reversed(rows)])
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_threads.context_failed", session_id=session_id, error=str(exc)[:300])
        return None


# ── the running summary ───────────────────────────────────────────────────────────────

FOLD_PROMPT = """You keep the running summary of a conversation between a facilities manager and an assistant.
Update the summary below with the new turns. Keep every durable fact the user stated or corrected
(buildings, assets, vendors, contracts, dates, figures, preferences, decisions, what is still open).
Drop pleasantries and anything the new turns superseded. Plain prose, at most {max_chars} characters,
no headings. Output only the new summary.

CURRENT SUMMARY:
{summary}

NEW TURNS:
{turns}"""


def schedule_fold(session_id: str) -> None:
    """Fold in the background; one fold per thread at a time."""
    if session_id in _folding:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _folding.add(session_id)

    async def _run():
        try:
            await fold_summary(session_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("chat_threads.fold_failed", session_id=session_id, error=str(exc)[:300])
        finally:
            _folding.discard(session_id)

    loop.create_task(_run())


async def _summarise(summary: str, turns: list[dict[str, Any]]) -> str:
    from ..config import settings
    from ..llm_factory import create_chat_model

    body = "\n".join(f"User: {_cut(t.get('question'), 1500)}\nAssistant: {_cut(t.get('answer'), 1500)}" for t in turns)
    llm = create_chat_model(model=settings.openai_model)
    out = await llm.ainvoke(FOLD_PROMPT.format(max_chars=SUMMARY_MAX_CHARS, summary=summary or "(none yet)", turns=body))
    text_ = out.content if hasattr(out, "content") else str(out)
    if isinstance(text_, list):
        text_ = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in text_)
    return _cut(str(text_), SUMMARY_MAX_CHARS)


async def fold_summary(session_id: str, *, force: bool = False) -> bool:
    """Fold the answered turns older than the verbatim window into the summary.

    Returns True when the summary changed. Nothing happens until FOLD_AFTER such turns have
    accumulated (or `force`), so the model is called once per handful of turns, not per turn."""
    if not _ready:
        return False
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        head = (await conn.execute(text(f"SELECT summary, summary_through, turn_count FROM {THREADS} WHERE id = :tid"),
                                   {"tid": session_id})).mappings().first()
        if head is None:
            return False
        through = int(head["summary_through"] or 0)
        rows = (await conn.execute(text(f"""
            SELECT turn_no, question, answer FROM {TURNS}
             WHERE thread_id = :tid AND turn_no > :after AND answer IS NOT NULL
             ORDER BY turn_no ASC"""), {"tid": session_id, "after": through})).mappings().all()
    older = [dict(r) for r in rows[:-CONTEXT_TURNS]] if len(rows) > CONTEXT_TURNS else []
    if not older or (len(older) < FOLD_AFTER and not force):
        return False
    new_summary = await _summarise(head["summary"] or "", older)
    if not new_summary:
        return False
    async with _get_engine().begin() as conn:
        await conn.execute(text(f"UPDATE {THREADS} SET summary = :s, summary_through = :t, updated_at = now() WHERE id = :tid"),
                           {"s": new_summary, "t": int(older[-1]["turn_no"]), "tid": session_id})
    log.info("chat_threads.folded", session_id=session_id, through=older[-1]["turn_no"])
    return True


# ── reading, for the navigator ────────────────────────────────────────────────────────

def _iso(d: dict[str, Any]) -> dict[str, Any]:
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            d[k] = str(v)
    return d


def _owner_sql(p: Principal, org: str | None) -> tuple[str, dict[str, Any]]:
    """A thread is its owner's, in the company they were acting for when they asked."""
    params: dict[str, Any] = {"uid": str(p.user_id)}
    if org:
        params["org"] = org
        return "user_id = CAST(:uid AS uuid) AND organization_id = CAST(:org AS uuid)", params
    return "user_id = CAST(:uid AS uuid) AND organization_id IS NULL", params


async def list_threads(*, limit: int = 60, q: str | None = None) -> list[dict[str, Any]]:
    if not _ready:
        return []
    p, org = _caller()
    if p is None:
        return []
    where, params = _owner_sql(p, org)
    if q and q.strip():
        where += " AND (title ILIKE :q OR summary ILIKE :q)"
        params["q"] = "%" + q.strip() + "%"
    params["lim"] = max(1, min(int(limit), 200))
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(f"""
            SELECT id, title, building_id, turn_count, created_at, last_message_at,
                   left(coalesce(summary, ''), 240) AS summary
              FROM {THREADS} WHERE deleted_at IS NULL AND {where}
             ORDER BY last_message_at DESC NULLS LAST LIMIT :lim"""), params)).mappings().all()
    return [_iso(dict(r)) for r in rows]


async def get_thread(session_id: str) -> dict[str, Any] | None:
    """The thread and its turns - for its owner only (a superadmin acting as that company too)."""
    if not _ready:
        return None
    p, org = _caller()
    if p is None:
        return None
    where, params = _owner_sql(p, org)
    params["tid"] = session_id
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        head = (await conn.execute(text(f"""
            SELECT id, title, building_id, turn_count, created_at, last_message_at, summary
              FROM {THREADS} WHERE id = :tid AND deleted_at IS NULL AND {where}"""), params)).mappings().first()
        if head is None:
            return None
        rows = (await conn.execute(text(f"""
            SELECT turn_no, question, answer, tools, citations, route, created_at, answered_at
              FROM {TURNS} WHERE thread_id = :tid ORDER BY turn_no ASC"""), {"tid": session_id})).mappings().all()
    out = _iso(dict(head))
    out["turns"] = [_iso(dict(r)) for r in rows]
    return out


async def rename_thread(session_id: str, title: str) -> bool:
    if not _ready:
        return False
    p, org = _caller()
    if p is None:
        return False
    where, params = _owner_sql(p, org)
    params.update({"tid": session_id, "title": _title(title)})
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"UPDATE {THREADS} SET title = :title, updated_at = now()"
                                      f" WHERE id = :tid AND deleted_at IS NULL AND {where}"), params)
    return bool(res.rowcount)


async def delete_thread(session_id: str) -> bool:
    """Hides the thread from its owner's list. The rows stay (the audit of what was asked)."""
    if not _ready:
        return False
    p, org = _caller()
    if p is None:
        return False
    where, params = _owner_sql(p, org)
    params["tid"] = session_id
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"UPDATE {THREADS} SET deleted_at = now(), updated_at = now()"
                                      f" WHERE id = :tid AND deleted_at IS NULL AND {where}"), params)
    return bool(res.rowcount)
