"""Chat memories - what the chat has learned about a company and its people (memory phase B).

Phase A keeps every conversation; this keeps what is worth carrying ACROSS conversations:

    fact        something the user stated about their estate that the records do not hold -
                "AHU-3 is the one the tenants call the big unit", "Apex invoices arrive on the 5th"
    correction  the user put the assistant right - "no, the boiler room is on level 3, not B1"
    preference  how this person likes answers - "costs in AED", "call it Bishopsgate, not B-301"

The sharing rule the user chose (2 Oct 2026): facts and corrections are the COMPANY's (every
colleague in the same company gets them, with who said it); preferences are the PERSON's.

How it runs:

* after each answered turn, `extract_and_store()` asks a small model what, if anything, in the
  exchange is durable - usually nothing. It is told to keep out everything the assistant computed
  (counts, costs, statuses are re-read live every time, never remembered) and every personal
  identifier. A guard drops anything that still looks like one.
* at the start of a turn, `recall()` ranks the caller's memories (their own plus the company's)
  against the question - cosine over text-embedding-3-small when the key is there, word overlap
  otherwise - and the orchestrator puts the best few in the message as "## What I remember".
* "what do you remember about me / us?" and "forget ..." are answered deterministically by
  `shortcut()`, with no model in the loop; the admin API lists and removes memories.

Embeddings are kept as JSON (no pgvector extension needed on either database): a company's
memories number hundreds, not millions, so ranking them in Python is instant. Scoped to the acting
company always; every write is best-effort and never breaks a turn.
"""
from __future__ import annotations

from contextvars import ContextVar

import asyncio
import json
import math
import re
import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import text

from ..http_client import caller_organization_id
from .principal import Principal, caller_principal

log = structlog.get_logger(__name__)

TABLE = "plenum_cafm.chat_memories"
KINDS = ("fact", "correction", "preference")
#: Memories recalled into a turn, and the similarity below which none is.
#: The recall block of the current turn, set once by the orchestrator when it reads the question
#: and read by every sub-agent and engine the turn then runs. Until 3 Oct 2026 the block reached
#: only the orchestrator's own prompt: a teaching saved from the trace never reached the udr
#: sub-agent that writes the SQL, so the same correction had to be typed every time.
turn_recall: ContextVar[str] = ContextVar("chat_turn_recall", default="")

RECALL_TOP = 6
RECALL_MIN = 0.30
#: Closer than this to an existing memory = the same memory, refreshed rather than duplicated.
DUPLICATE_AT = 0.90
MAX_PER_TURN = 4
MAX_TEXT = 300
EMBED_MODEL = "text-embedding-3-small"
_ready = False

#: Identity numbers, phone numbers, emails, long digit runs: never a memory.
_PERSONAL = re.compile(r"(\b\d[\d \-]{7,}\d\b|\b[A-Z]{1,2}\d{6,9}\b|\b784-?\d{4}-?\d{7}-?\d\b|[\w.+-]+@[\w-]+\.[\w.]+|"
                       r"\b(passport|visa|emirates id|labour card|iban|salary)\b)", re.I)

_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        id               UUID PRIMARY KEY,
        organization_id  UUID,
        user_id          UUID,
        kind             TEXT NOT NULL,
        text             TEXT NOT NULL,
        subject          TEXT,
        embedding        JSONB,
        source_thread    TEXT,
        source_turn      INTEGER,
        created_by       UUID,
        created_by_email TEXT,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        last_used_at     TIMESTAMPTZ,
        use_count        INTEGER NOT NULL DEFAULT 0,
        deleted_at       TIMESTAMPTZ,
        deleted_by       UUID
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_chat_memories_org ON {TABLE} (organization_id, deleted_at, user_id)",
    # Approval (5 Oct 2026): a teaching from someone who is not an admin waits as `pending` and is
    # not recalled until an admin approves it; a rejected one is hidden. Admins' teachings and
    # everyone's own preferences are active at once.
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active'",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS reviewed_by UUID",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS reviewed_by_email TEXT",
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ",
]

STATUSES = ("active", "pending", "rejected")


def needs_approval(principal: Principal, kind: str) -> bool:
    """A company teaching (fact or correction) from a non-admin waits for an admin."""
    return kind != "preference" and not principal.is_admin


def ready() -> bool:
    return _ready


async def ensure_tables() -> bool:
    global _ready
    try:
        from ..database import _get_engine

        async with _get_engine().begin() as conn:
            for stmt in _DDL:
                await conn.execute(text(stmt))
        _ready = True
        log.info("chat_memories.ready")
    except Exception as exc:  # noqa: BLE001
        _ready = False
        log.warning("chat_memories.tables_failed", error=str(exc)[:300])
    return _ready


def _caller() -> tuple[Principal | None, str | None]:
    p = caller_principal.get()
    org = caller_organization_id.get() or (str(p.organization_id) if p and p.organization_id else None)
    return p, org


# ── similarity ────────────────────────────────────────────────────────────────────────

async def embed(texts: list[str]) -> list[list[float]] | None:
    """Vectors for the texts, or None when no embedding key is configured or the call fails."""
    from ..config import settings

    if not texts or not (settings.openai_api_key or "").strip():
        return None
    try:
        import openai

        base = (settings.openai_api_base or "").strip() or None
        client = openai.AsyncOpenAI(api_key=settings.openai_api_key, base_url=base)
        resp = await client.embeddings.create(model=EMBED_MODEL, input=[t[:2000] for t in texts])
        return [d.embedding for d in resp.data]
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_memories.embed_failed", error=str(exc)[:200])
        return None


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "what", "which", "do", "does",
         "with", "about", "from", "by", "me", "my", "our", "we", "it", "this", "that", "those", "these", "them",
         "how", "many", "at", "was", "were", "be", "you", "i", "us", "please", "show", "tell", "give"}


def words(s: str) -> set[str]:
    """Content words, crudely stemmed (prefers/prefer, invoices/invoice) so a fallback match is not lost to a plural."""
    out = set()
    for w in re.findall(r"[a-z0-9][a-z0-9\-]{1,}", (s or "").lower()):
        if w in _STOP:
            continue
        out.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return out


def overlap(q: str, m: str) -> float:
    """Word-overlap fallback when there are no vectors: how much of the shorter side the two share."""
    a, b = words(q), words(m)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def score(question: str, q_vec: list[float] | None, memory: dict[str, Any]) -> float:
    m_vec = memory.get("embedding")
    if isinstance(m_vec, str):
        try:
            m_vec = json.loads(m_vec)
        except ValueError:
            m_vec = None
    if q_vec and m_vec:
        return cosine(q_vec, m_vec)
    return overlap(question, memory.get("text") or "") * 0.8


# ── reading ───────────────────────────────────────────────────────────────────────────

def _visible_sql(p: Principal, org: str | None, *, include_pending: bool = False) -> tuple[str, dict[str, Any]]:
    """The company's shared memories plus this person's own. Active ones only - what the chat
    recalls - unless the caller asks for the pending ones too (the admin page; a person sees
    their own pending teachings there)."""
    params: dict[str, Any] = {"uid": str(p.user_id)}
    st = (" AND (status = 'active' OR (status = 'pending' AND (created_by = CAST(:uid AS uuid) OR :admin)))"
          if include_pending else " AND status = 'active'")
    if include_pending:
        params["admin"] = bool(p.is_admin)
    if org:
        params["org"] = org
        return ("organization_id = CAST(:org AS uuid) AND deleted_at IS NULL"
                " AND (user_id IS NULL OR user_id = CAST(:uid AS uuid))" + st), params
    return "organization_id IS NULL AND deleted_at IS NULL AND user_id = CAST(:uid AS uuid)" + st, params


async def visible(limit: int = 2000, *, include_pending: bool = False) -> list[dict[str, Any]]:
    if not _ready:
        return []
    p, org = _caller()
    if p is None:
        return []
    where, params = _visible_sql(p, org, include_pending=include_pending)
    params["lim"] = limit
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text(f"""
            SELECT id::text, kind, text, subject, embedding, user_id::text AS user_id, created_by_email, created_at,
                   last_used_at, use_count, source_thread, status, created_by::text AS created_by,
                   reviewed_by_email, reviewed_at
              FROM {TABLE} WHERE {where} ORDER BY (status = 'pending') DESC, created_at DESC LIMIT :lim"""), params)).mappings().all()
    return [dict(r) for r in rows]


async def review(memory_id: str, decision: str) -> bool:
    """An admin approves a pending teaching (it is recalled from now on) or rejects it (hidden)."""
    if not _ready or decision not in ("approve", "reject"):
        return False
    p, org = _caller()
    if p is None or not p.is_admin:
        return False
    where, params = _visible_sql(p, org, include_pending=True)
    params.update({"id": memory_id, "by": str(p.user_id), "email": p.email})
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        if decision == "approve":
            res = await conn.execute(text(f"UPDATE {TABLE} SET status = 'active', reviewed_by = CAST(:by AS uuid), reviewed_by_email = :email,"
                                          f" reviewed_at = now() WHERE id = CAST(:id AS uuid) AND status = 'pending' AND {where}"), params)
        else:
            res = await conn.execute(text(f"UPDATE {TABLE} SET status = 'rejected', deleted_at = now(), deleted_by = CAST(:by AS uuid),"
                                          f" reviewed_by = CAST(:by AS uuid), reviewed_by_email = :email, reviewed_at = now()"
                                          f" WHERE id = CAST(:id AS uuid) AND status = 'pending' AND {where}"), params)
    return bool(res.rowcount)


async def recall(question: str, *, top: int = RECALL_TOP) -> list[dict[str, Any]]:
    """The memories that bear on this question, best first; marks them used."""
    if not _ready or len((question or "").strip()) < 3:
        return []
    try:
        mems = await visible()
        if not mems:
            return []
        vecs = await embed([question]) if any(m.get("embedding") for m in mems) else None
        q_vec = vecs[0] if vecs else None
        ranked = sorted(((score(question, q_vec, m), m) for m in mems), key=lambda x: -x[0])
        hits = [m | {"score": round(s, 3)} for s, m in ranked[:top] if s >= RECALL_MIN]
        if hits:
            from ..database import _get_engine

            async with _get_engine().begin() as conn:
                await conn.execute(text(f"UPDATE {TABLE} SET last_used_at = now(), use_count = use_count + 1"
                                        " WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": [h["id"] for h in hits]})
        return hits
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_memories.recall_failed", error=str(exc)[:300])
        return []


def format_recall(hits: list[dict[str, Any]]) -> str:
    """The block the model reads. Figures are never in here, so it is told to re-read them."""
    if not hits:
        return ""
    lines = ["## What I remember (from earlier conversations - things people told us, not figures; "
             "re-read live data for any count, cost or status)"]
    for h in hits:
        who = "you" if h.get("user_id") else ("a colleague" if not h.get("created_by_email") else h["created_by_email"])
        when = h["created_at"].strftime("%d %b %Y") if isinstance(h.get("created_at"), datetime) else ""
        scope = "your preference" if h.get("kind") == "preference" else f"{h.get('kind')}, company-wide"
        lines.append(f"- {h['text']} ({scope}; from {who}{', ' + when if when else ''})")
    return "\n".join(lines)


# ── writing ───────────────────────────────────────────────────────────────────────────

def looks_personal(s: str) -> bool:
    return bool(_PERSONAL.search(s or ""))


def clean(items: Any) -> list[dict[str, Any]]:
    """The model's extraction, kept to what the rules allow."""
    out: list[dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        kind = str(it.get("kind") or "").strip().lower()
        txt = " ".join(str(it.get("text") or "").split())
        if kind not in KINDS or len(txt) < 8 or looks_personal(txt):
            continue
        out.append({"kind": kind, "text": txt[:MAX_TEXT], "subject": (str(it.get("subject") or "").strip()[:80] or None)})
        if len(out) >= MAX_PER_TURN:
            break
    return out


EXTRACT_PROMPT = """You decide what, if anything, in one exchange between a facilities manager and an assistant is worth
remembering for FUTURE conversations. Output a JSON array (often empty: []), each item
{{"kind": "fact|correction|preference", "text": "...", "subject": "building/vendor/asset it is about or null"}}.

Remember ONLY:
- fact: something the USER stated about their estate, vendors, contracts, people or processes that a
  database would not hold - a nickname, a local arrangement, who handles what, a standing instruction.
- correction: the user corrected the assistant or the records ("no, X is actually Y").
- preference: how this person wants answers - currency, units, names, format, level of detail.

NEVER remember: anything the ASSISTANT computed or reported (counts, costs, dates, statuses, lists of
work orders - these are re-read live every time); the question itself; anything speculative; any
personal identifier (passport, visa, Emirates ID, phone, email, salary). One sentence each, in the
third person ("The user prefers...", "At Bishopsgate Tower, ..."). At most {max_items} items.

USER: {question}

ASSISTANT: {answer}"""


async def _extract(question: str, answer: str) -> list[dict[str, Any]]:
    from ..config import settings
    from ..llm_factory import create_chat_model

    llm = create_chat_model(model=settings.openai_model)
    out = await llm.ainvoke(EXTRACT_PROMPT.format(max_items=MAX_PER_TURN, question=question[:3000], answer=answer[:4000]))
    body = out.content if hasattr(out, "content") else str(out)
    if isinstance(body, list):
        body = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in body)
    m = re.search(r"\[.*\]", str(body), re.S)
    try:
        return clean(json.loads(m.group(0)) if m else [])
    except ValueError:
        return []


async def store(items: list[dict[str, Any]], *, principal: Principal, org: str | None,
                source_thread: str | None = None, source_turn: int | None = None) -> int:
    """Insert what is new; a near-duplicate of an existing memory is refreshed instead. Returns inserts."""
    if not _ready or not items:
        return 0
    existing = await visible(include_pending=True)
    vecs = await embed([i["text"] for i in items])
    ex_vecs = any(e.get("embedding") for e in existing)
    inserted = 0
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        for n, it in enumerate(items):
            vec = vecs[n] if vecs else None
            dup = None
            for e in existing:
                s = score(it["text"], vec if ex_vecs else None, e) if (vec and ex_vecs) else overlap(it["text"], e.get("text") or "")
                if s >= DUPLICATE_AT or (e.get("text") or "").lower() == it["text"].lower():
                    dup = e
                    break
            if dup:
                await conn.execute(text(f"UPDATE {TABLE} SET last_used_at = now(), use_count = use_count + 1 WHERE id = CAST(:id AS uuid)"),
                                   {"id": dup["id"]})
                continue
            # The sharing rule: a preference is the person's; a fact or correction is the company's.
            owner = str(principal.user_id) if it["kind"] == "preference" else None
            await conn.execute(text(f"""
                INSERT INTO {TABLE} (id, organization_id, user_id, kind, text, subject, embedding, source_thread, source_turn,
                                     created_by, created_by_email, status)
                VALUES (CAST(:id AS uuid), CAST(:org AS uuid), CAST(:uid AS uuid), :kind, :text, :subject,
                        CAST(:emb AS jsonb), :thread, :turn, CAST(:by AS uuid), :email, :status)"""),
                {"id": str(uuid.uuid4()), "org": org, "uid": owner, "kind": it["kind"], "text": it["text"],
                 "subject": it.get("subject"), "emb": json.dumps(vec) if vec else None, "thread": source_thread,
                 "turn": source_turn, "by": str(principal.user_id), "email": principal.email,
                 "status": "pending" if needs_approval(principal, it["kind"]) else "active"})
            inserted += 1
    return inserted


async def extract_and_store(session_id: str, question: str, answer: str, turn_no: int | None = None) -> int:
    """One turn's durable learning, if any. Called in the background after an answer is recorded."""
    if not _ready or not (question or "").strip() or not (answer or "").strip():
        return 0
    p, org = _caller()
    if p is None:
        return 0
    try:
        items = await _extract(question, answer)
        if not items:
            return 0
        n = await store(items, principal=p, org=org, source_thread=session_id, source_turn=turn_no)
        if n:
            log.info("chat_memories.learned", session_id=session_id, count=n, kinds=[i["kind"] for i in items])
        return n
    except Exception as exc:  # noqa: BLE001
        log.warning("chat_memories.extract_failed", session_id=session_id, error=str(exc)[:300])
        return 0


def learn_soon(session_id: str, question: str, answer: str, turn_no: int | None = None) -> None:
    from ..agents.replay_guard import is_replay
    if is_replay():  # a skill-lab replay's answer is not something the company said
        return
    try:
        asyncio.get_running_loop().create_task(extract_and_store(session_id, question, answer, turn_no))
    except RuntimeError:
        pass


async def forget(memory_id: str) -> bool:
    """Soft-delete one memory the caller may see. A colleague's company fact can be forgotten by an admin only."""
    if not _ready:
        return False
    p, org = _caller()
    if p is None:
        return False
    where, params = _visible_sql(p, org)
    if not p.is_admin:
        where += " AND (user_id = CAST(:uid AS uuid) OR created_by = CAST(:uid AS uuid))"
    params.update({"id": memory_id, "by": str(p.user_id)})
    from ..database import _get_engine

    async with _get_engine().begin() as conn:
        res = await conn.execute(text(f"UPDATE {TABLE} SET deleted_at = now(), deleted_by = CAST(:by AS uuid)"
                                      f" WHERE id = CAST(:id AS uuid) AND {where}"), params)
    return bool(res.rowcount)


# ── the two things said in chat ───────────────────────────────────────────────────────

_WHAT = re.compile(r"\bwhat (?:do|did) you (?:remember|know) about (?:me|us|our company|this company|my company)\b|"
                   r"\bwhat have you (?:learned|learnt|remembered)\b|\bshow (?:me )?(?:your|the) memor(?:y|ies)\b|"
                   r"\bwhat do you remember\b", re.I)
_FORGET = re.compile(r"^\s*(?:please\s+)?forget\s+(?:that|this|it|about\s+)?(.*)$", re.I | re.S)


def _tool_calls(name: str, out: Any) -> list[dict[str, Any]]:
    return [{"tool": name, "input": {}, "output": json.dumps(out, default=str)[:4000]}]


async def shortcut(user_message: str, session_id: str) -> dict[str, Any] | None:
    """A deterministic answer for 'what do you remember' and 'forget ...'; None for anything else."""
    msg = (user_message or "").strip()
    if not msg or not _ready:
        return None
    if _WHAT.search(msg):
        mems = await visible(limit=200)
        if not mems:
            answer = "Nothing yet. I remember things you tell me about your estate, corrections you make, and how you like answers - not figures, which I re-read every time."
        else:
            mine = [m for m in mems if m.get("user_id")]
            shared = [m for m in mems if not m.get("user_id")]
            parts = []
            if mine:
                parts.append("**Your preferences**\n" + "\n".join(f"- {m['text']}" for m in mine[:30]))
            if shared:
                parts.append("**About your company (shared with colleagues)**\n" + "\n".join(
                    f"- {m['text']}" + (f" _(from {m['created_by_email']})_" if m.get("created_by_email") else "") for m in shared[:60]))
            parts.append('Say "forget <what>" to remove one. Figures (counts, costs, statuses) are never remembered; they are re-read live.')
            answer = "\n\n".join(parts)
        return {"session_id": session_id, "answer": answer, "tool_calls": _tool_calls("list_memories", {"count": len(mems)}),
                "success": True, "interrupted": False, "route_intent": "memory"}
    m = _FORGET.match(msg)
    if m:
        what = (m.group(1) or "").strip(" .!?")
        mems = await visible(limit=500)
        p, _ = _caller()
        if not mems:
            answer = "There is nothing remembered to forget."
        elif not what or what.lower() in ("everything", "all", "all of it", "everything about me"):
            n = 0
            for mem in mems:
                if p and (mem.get("user_id") or p.is_admin or True):
                    n += 1 if await forget(mem["id"]) else 0
            answer = f"Forgotten {n} memor{'y' if n == 1 else 'ies'}."
        else:
            vecs = await embed([what]) if any(x.get("embedding") for x in mems) else None
            best = max(((score(what, vecs[0] if vecs else None, x), x) for x in mems), key=lambda t: t[0])
            if best[0] >= 0.45:
                ok = await forget(best[1]["id"])
                answer = (f"Forgotten: \"{best[1]['text']}\"." if ok else
                          f"I can't remove that one - \"{best[1]['text']}\" was recorded by a colleague; an admin can.")
            else:
                answer = f"I couldn't find a memory matching \"{what}\". Ask \"what do you remember about us?\" to see the list."
        return {"session_id": session_id, "answer": answer, "tool_calls": _tool_calls("forget_memory", {"query": what}),
                "success": True, "interrupted": False, "route_intent": "memory"}
    return None
