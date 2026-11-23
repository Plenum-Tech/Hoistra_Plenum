"""Memorilla - the local memory server (plenum-memory) as the one place recall reads from (20 Nov 2026).

On when PLENUM_MEMORY_URL is set (for example http://127.0.0.1:8000); off otherwise, and then
nothing here runs and chat_memories ranks memories as before.

Each company has two collections on the server:

    hoistra-<org8>-memories   one entry per chat memory (fact, correction, preference), its text kept
                              as chat_memories holds it, so a hit is matched back to its row
    hoistra-<org8>-plans      one entry per question a good turn answered: "Question: ... | Plan ..."

What it does:

* `search_memories()` - the company's entries closest to a question. chat_memories.recall() keeps
  only hits that are memories the caller may see (visible()), so a pending, deleted or another
  person's preference is never recalled from here.
* `recall_plan()` - the plan of an earlier good turn whose question is close to this one. The
  server says which past question is closest; the plan itself is read from that turn's own plan
  span in the trace tables, and is checked again (planner.validate) against today's tools before
  it is used. Reused only when it is close AND clearly closer than any other plan:
  the local test (20 Nov 2026) had right matches as low as 0.67 and a wrong one at 0.79, so a score
  alone cannot decide.
* `add_memory()` / `add_plan()` - new entries, after a memory is stored or a good turn ends.

Every call is best-effort with a short timeout: Memorilla being down never breaks a turn.
"""
from __future__ import annotations

import json
import os
import re
import time
from contextvars import ContextVar
from typing import Any

import structlog
from sqlalchemy import text

log = structlog.get_logger(__name__)

#: Seconds a call may take. The server embeds the question on CPU: about 0.5-3 s on a laptop.
TIMEOUT = float(os.environ.get("PLENUM_MEMORY_TIMEOUT", "8"))
#: Hits asked for: enough for RECALL_TOP memories after the visibility filter.
MEMORY_K = 12
#: A memory below this similarity is not recalled (Qwen3-Embedding cosine; unrelated text sits near 0.3-0.45).
MEMORY_MIN = float(os.environ.get("PLENUM_MEMORY_MIN", "0.45"))
#: A past plan is reused only at this similarity or above ...
PLAN_MIN = float(os.environ.get("PLENUM_PLAN_MIN", "0.65"))
#: ... and only when the closest plan that differs from it is at least this much further away.
#: 0.04 since the 27-question search test (20 Nov 2026): 0.05 held back two right matches
#: (margins 0.048 and 0.044); the one wrong match it has to stop was 0.024 ahead.
PLAN_MARGIN = float(os.environ.get("PLENUM_PLAN_MARGIN", "0.04"))

#: The recalled turn's tools: {"question", "from_turn", "tools": [{tool, engine, args}]} - set when a
#: past plan is reused, read where an engine's prompt is built, cleared at the start of every turn.
turn_recipe: ContextVar[dict | None] = ContextVar("memorilla_turn_recipe", default=None)
#: Tools that are the loop's own plumbing, not a read of the records: never part of a recipe.
META_TOOLS = frozenset({"task", "select_skill", "memory_set", "write_todos", "think", "compact_context"})
#: Most tool calls a recipe carries, and characters of one call's arguments shown.
RECIPE_MAX, ARGS_MAX = 8, 300

_PLAN_LINE = re.compile(r"^Question:\s*(?P<q>.*?)\s*\|\s*Plan\b(?P<plan>.*)$", re.S)


def url() -> str:
    return (os.environ.get("PLENUM_MEMORY_URL") or "").strip().rstrip("/")


def enabled() -> bool:
    return bool(url())


def collection(org: str | None, what: str) -> str:
    return f"hoistra-{(org or 'none')[:8]}-{what}"


def memory_text(kind: str, text_: str, subject: str | None = None, *, personal: bool = False) -> str:
    """The entry a memory is kept as. Its own text appears whole in it, which is how a hit is matched back."""
    scope = "a user's preference" if personal else f"company {kind}"
    return f"[{scope}] {text_}" + (f" Subject: {subject}." if subject else "")


def plan_text(question: str, plan: dict[str, Any]) -> str:
    parts = []
    for s in plan.get("steps") or []:
        dep = f" after {', '.join(s['depends_on'])}" if s.get("depends_on") else ""
        ask = f" asking: {s['ask']}" if s.get("ask") else ""
        parts.append(f"{s.get('id')}: {s.get('kind')} {s.get('target')}{dep}{ask}")
    return f"Question: {question.strip()} | Plan ({plan.get('mode') or 'single'}): {'; '.join(parts)}"


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any] | None:
    if not enabled():
        return None
    import httpx

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url() + path, json=body)
        r.raise_for_status()
        return r.json()


async def search(user: str, question: str, k: int) -> list[dict[str, Any]]:
    """[{similarity, index, text}] best first; raises when the server fails (the caller falls back)."""
    data = await _post("/v1/memory/search", {"user": user, "question": question, "k": k})
    return list((data or {}).get("hits") or [])


async def search_memories(org: str | None, question: str) -> list[dict[str, Any]]:
    return await search(collection(org, "memories"), question, MEMORY_K)


async def add(user: str, texts: list[str]) -> int:
    """Best-effort: the number added, 0 when off or failed."""
    texts = [t for t in texts if (t or "").strip()]
    if not texts or not enabled():
        return 0
    try:
        data = await _post("/v1/memory/documents", {"user": user, "documents": texts, "chunk": False})
        return int((data or {}).get("added") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("memorilla.add_failed", user=user, error=str(exc)[:200])
        return 0


async def add_memory(org: str | None, kind: str, text_: str, subject: str | None, *, personal: bool) -> int:
    return await add(collection(org, "memories"), [memory_text(kind, text_, subject, personal=personal)])


async def add_plan(org: str | None, question: str, plan: dict[str, Any]) -> int:
    return await add(collection(org, "plans"), [plan_text(question, plan)])


# ── plans ─────────────────────────────────────────────────────────────────────────────────

def parse_plan_hit(hit_text: str) -> tuple[str, str] | None:
    """(the past question, the plan as written) from a plans entry."""
    m = _PLAN_LINE.match((hit_text or "").strip())
    return (m.group("q").strip(), m.group("plan").strip()) if m else None


def choose_plan_hit(hits: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """The hit to reuse, or None and why not. Close enough, and clearly closer than any hit whose plan differs."""
    if not hits:
        return None, "no past plans"
    top = hits[0]
    parsed = parse_plan_hit(top.get("text") or "")
    if parsed is None:
        return None, "the closest entry is not a plan"
    sim = float(top.get("similarity") or 0)
    if sim < PLAN_MIN:
        return None, f"closest past question at {sim:.3f}, below {PLAN_MIN}"
    other = next((h for h in hits[1:] if (parse_plan_hit(h.get("text") or "") or ("", ""))[1] != parsed[1]), None)
    if other is not None and sim - float(other.get("similarity") or 0) < PLAN_MARGIN:
        return None, (f"closest past question at {sim:.3f} but a different plan is at {float(other['similarity']):.3f}"
                      f" (margin below {PLAN_MARGIN})")
    return top, "ok"


# ── whether a stored plan was made under today's rules (20 Nov 2026) ──────────────────────
# A plan is what the router and planner decided under the rules of the day: the router's prompt,
# its catalogue of agents, the planner's prompt. When those change, an old plan can be an old
# mistake: "Which contracts come up for renewal in the next 90 days?" was routed to compliance on
# 12 Nov, the same day the router was told a contract renewal is a contract question - and was
# still being replayed to compliance by plan recall (Qwen A/B, P14). So every plan span is stamped
# with a fingerprint of those rules (trace.on_plan), and a stored plan is reused only while the
# fingerprint is today's. A plan from before stamping is reused only when its turn is on or after
# PLENUM_PLANS_SINCE (an ISO date); unset, it is not reused.

_rules: str | None = None


def rules_fingerprint() -> str:
    """12 hex characters naming the routing and planning rules this process runs on."""
    global _rules
    if _rules is None:
        import hashlib

        from ..agents import agent_router, planner

        catalogue = agent_router._catalogue(None)[0]
        text_ = "\n\x1e".join([agent_router._PROMPT, catalogue, planner.PLAN_PROMPT, planner.RECORDS_RULES])
        _rules = hashlib.sha256(text_.encode("utf-8")).hexdigest()[:12]
    return _rules


def plans_since() -> str | None:
    return (os.environ.get("PLENUM_PLANS_SINCE") or "").strip() or None


def stale_reason(rules: str | None, started_at: Any, *, current: str, since: str | None) -> str | None:
    """Why a stored plan may not be reused today, or None when it may."""
    if rules:
        return None if rules == current else f"the routing rules changed since that plan ({rules} then, {current} now)"
    if not since:
        return "the plan predates rule stamps and PLENUM_PLANS_SINCE is not set"
    from datetime import date, datetime

    try:
        cut = datetime.fromisoformat(since)
    except ValueError:
        return f"PLENUM_PLANS_SINCE is not a date: {since!r}"
    when = started_at if isinstance(started_at, datetime) else datetime.fromisoformat(str(started_at)) if started_at else None
    if when is None:
        return "the plan's turn has no time"
    if when.tzinfo is not None and cut.tzinfo is None:
        cut = cut.replace(tzinfo=when.tzinfo)
    return None if when >= cut else f"the plan predates rule stamps and is older than PLENUM_PLANS_SINCE ({since})"


def check_plan(plan: dict[str, Any], *, validate, engines, allowed) -> tuple[dict[str, Any] | None, str | None]:
    """A past plan checked against today. A one-engine plan from the router carries no `ask` - the
    turn is sent to that engine whole - so the planner's step check (which wants one) does not fit
    it: it is reused when that engine is still known and part of this space. Any other plan goes
    through the planner's own `validate`. (Live check, 20 Nov 2026: every one-engine plan was
    refused for "no ask" before this.)"""
    steps = plan.get("steps") or []
    if len(steps) == 1 and steps[0].get("kind") == "engine" and not steps[0].get("ask"):
        target = steps[0].get("target")
        if target not in engines:
            return None, f"unknown engine {target!r}"
        if target not in allowed:
            return None, f"engine {target!r} is not part of this space"
        return plan, None
    return validate(plan)


async def plan_of_question(org: str | None, question: str) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    """The plan of the latest good turn that asked exactly this question: (plan, turn_id, meta), meta
    being {"rules": the rules fingerprint it was stamped with or None, "started_at"}.

    Good = finished ok, no thumbs-down, and no verify step failed - the same rule the export used."""
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        row = (await conn.execute(text("""
            SELECT p.output->'plan' AS plan, t.turn_id, p.input->>'rules' AS rules, t.started_at
              FROM plenum_cafm.agent_trace_turns t
              JOIN plenum_cafm.agent_trace_spans p ON p.turn_id = t.turn_id AND p.kind = 'plan'
             WHERE t.ok AND coalesce(t.feedback_rating, '') <> 'down'
               AND (CAST(:org AS uuid) IS NULL OR t.organization_id = CAST(:org AS uuid))
               AND lower(btrim(t.question)) = lower(btrim(:q))
               AND p.output->'plan' IS NOT NULL AND p.output->'plan' <> 'null'::jsonb
               AND NOT EXISTS (SELECT 1 FROM plenum_cafm.agent_trace_spans v
                                WHERE v.turn_id = t.turn_id AND v.kind = 'step' AND NOT v.ok)
             ORDER BY t.started_at DESC, p.seq DESC LIMIT 1"""), {"org": org, "q": question})).first()
    if row is None:
        return None, None, {}
    plan = row[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    return (plan if isinstance(plan, dict) else None), row[1], {"rules": row[2], "started_at": row[3]}


async def tools_of_turn(turn_id: str) -> list[dict[str, Any]]:
    """The tools a past turn called that finished ok, in order: [{tool, engine, args}]. Read from its
    tool spans; the loop's own plumbing (META_TOOLS) is left out."""
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        rows = (await conn.execute(text("""
            SELECT name, agent, input FROM plenum_cafm.agent_trace_spans
             WHERE turn_id = :t AND kind = 'tool' AND ok AND coalesce(agent, '') <> 'tool:meta'
             ORDER BY seq"""), {"t": turn_id})).all()
    out = []
    for name, agent, args in rows:
        if name in META_TOOLS:
            continue
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"_": args}
        out.append({"tool": name, "engine": agent, "args": args if isinstance(args, dict) else {}})
    return out


def recipe_block(recipe: list[dict[str, Any]], tool_names: list[str], past_question: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    """The engine prompt's "how this was answered before" block and the calls it names: only the
    tools this engine has, a repeat in a row once, at most RECIPE_MAX. The arguments are shown as
    the earlier question's, to be set again from this one - an earlier question about August must
    not make this one about September read August."""
    calls: list[dict[str, Any]] = []
    for c in recipe or []:
        if c.get("tool") not in tool_names:
            continue
        if calls and calls[-1]["tool"] == c["tool"] and calls[-1]["args"] == c.get("args"):
            continue
        calls.append({"tool": c["tool"], "args": c.get("args") or {}})
        if len(calls) >= RECIPE_MAX:
            break
    if not calls:
        return "", []
    lines = ["# How an earlier question like this was answered",
             (f'The question "{past_question}" was answered well by calling these tools, in this order:'
              if past_question else "An earlier question like this was answered well by calling these tools, in this order:")]
    for n, c in enumerate(calls, 1):
        args = json.dumps(c["args"], ensure_ascii=False, default=str)
        lines.append(f"{n}. {c['tool']}({args if len(args) <= ARGS_MAX else args[:ARGS_MAX] + ' …'})")
    lines.append("Call the same tools in the same order unless this question clearly needs something else. "
                 "Set every argument from THIS question - its buildings, assets, vendors, dates and periods - "
                 "never copy a value from the earlier one that this question does not state. "
                 "The figures still come from what the tools return now.")
    return "\n".join(lines), calls


async def recall_plan(org: str | None, question: str, *, validate) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """(the plan to run, what happened). `validate(plan)` -> (plan | None, why), the planner's own check.

    The plan is None when nothing close enough was found, its turn's plan cannot be read, or it no
    longer passes the planner's checks; `info` says which, for the trace span either way."""
    t0 = time.perf_counter()
    info: dict[str, Any] = {"collection": collection(org, "plans"), "question": question}
    try:
        hits = await search(collection(org, "plans"), question, 5)
        info["hits"] = [{"similarity": round(float(h.get("similarity") or 0), 4), "text": (h.get("text") or "")[:240]} for h in hits]
        top, why = choose_plan_hit(hits)
        if top is None:
            info["used"], info["why"] = False, why
            return None, info
        past_q = parse_plan_hit(top["text"])[0]
        info["matched_question"], info["similarity"] = past_q, round(float(top["similarity"]), 4)
        plan, turn_id, meta = await plan_of_question(org, past_q)
        info["from_turn"] = turn_id
        if plan is None:
            info["used"], info["why"] = False, "the past turn's plan was not found in the trace tables"
            return None, info
        current = rules_fingerprint()
        info["rules"] = {"plan": meta.get("rules"), "now": current, "plan_made": str(meta.get("started_at") or "")}
        stale = stale_reason(meta.get("rules"), meta.get("started_at"), current=current, since=plans_since())
        if stale:
            info["used"], info["why"] = False, stale
            return None, info
        checked, why = validate({k: v for k, v in plan.items() if k != "areas"})
        if checked is None:
            info["used"], info["why"] = False, f"the past plan no longer validates: {why}"
            return None, info
        info["used"], info["why"] = True, "ok"
        try:
            info["tools"] = await tools_of_turn(turn_id)
        except Exception as exc:  # noqa: BLE001 - the plan stands without its recipe
            info["tools"], info["tools_error"] = [], str(exc)[:200]
        return checked, info
    except Exception as exc:  # noqa: BLE001 - recall never breaks a turn
        info["used"], info["error"] = False, str(exc)[:300]
        log.warning("memorilla.recall_plan_failed", error=str(exc)[:200])
        return None, info
    finally:
        info["ms"] = round((time.perf_counter() - t0) * 1000, 1)


# ── latent recall: Memorilla's memory tokens (23 Nov 2026) ───────────────────────────────────
#
# Everything above uses the server as a similarity search: the closest entries, as text. Latent
# recall is what Memorilla is for: the server reads a whole set of memories into its 16 memory
# tokens and its decoder (Qwen3-8B) writes a short note of what bears on the question - things
# spread over many memories, or never stated in one, that a top-k search does not surface.
#
# The caller SENDS the memories to read. The server's company collection holds every memory of the
# company - other people's preferences, ones still pending approval - and a note blends what it
# reads, so it cannot be filtered afterwards the way search hits are. chat_memories.latent_recall
# sends exactly visible()'s memories: latent recall never reads more than search may return.
#
# Server contract (the plenum-memory server, outside this repository):
#
#   POST /v1/memory/recall
#     {"question": str, "documents": [str, ...], "instructions": str, "max_tokens": int}
#   -> {"note": str, "ms": float?, "model": str?}
#
#   It writes the documents into Memorilla's memory (cached by their content, so the same set is not
#   encoded twice) and answers the question with `instructions` as the system prompt, e.g.
#   MemoryVLLM.answer(question, model.write(documents), system_prompt=instructions).
#
# PLENUM_MEMORY_LATENT: off (default) | shadow - asked for and traced only, the turn never waits
# for it | live - the note joins the recall block the model reads. Off whenever PLENUM_MEMORY_URL is.

#: Seconds the recall call may take (decoder on a GPU: about 1-3 s).
LATENT_TIMEOUT = float(os.environ.get("PLENUM_MEMORY_LATENT_TIMEOUT", "6"))
#: Most memories sent in one call (visible() is newest first, so the oldest are left out).
LATENT_MAX_DOCS = int(os.environ.get("PLENUM_MEMORY_LATENT_DOCS", "400"))
#: The note's length ceiling, in decoder tokens, and in characters once back.
LATENT_MAX_TOKENS = int(os.environ.get("PLENUM_MEMORY_LATENT_TOKENS", "200"))
LATENT_MAX_CHARS = 1500

RECALL_INSTRUCTIONS = (
    "You are the memory of a facilities-management assistant. The memories are facts, corrections and "
    "preferences people told it in earlier conversations. Write at most 5 short lines stating only what "
    "in the memories bears on the question - rules, corrections, preferences, names - each line one "
    "memory's point in plain words. Never state a count, cost, date or status as current: those are "
    "re-read from live data. If nothing in the memories bears on the question, write exactly: nothing relevant"
)

_NOTHING = re.compile(r"^\W*(nothing relevant|nothing|none|no relevant memor)", re.I)


def latent_mode() -> str:
    """'off' | 'shadow' | 'live'."""
    mode = (os.environ.get("PLENUM_MEMORY_LATENT") or "off").strip().lower()
    return mode if mode in ("shadow", "live") and enabled() else "off"


async def recall_note(question: str, documents: list[str]) -> dict[str, Any]:
    """Memorilla's note over `documents` for `question`: {"note", "documents", "server_ms", "model"}.

    "note" is "" when nothing bears on the question. Raises when the server fails or is off - the
    caller traces the failure and the turn goes on without the note."""
    if not enabled():
        raise RuntimeError("PLENUM_MEMORY_URL is not set")
    docs = [d for d in documents if (d or "").strip()][:LATENT_MAX_DOCS]
    if not docs or len((question or "").strip()) < 3:
        return {"note": "", "documents": len(docs), "server_ms": None, "model": None}
    import httpx

    async with httpx.AsyncClient(timeout=LATENT_TIMEOUT) as client:
        r = await client.post(url() + "/v1/memory/recall",
                              json={"question": question.strip(), "documents": docs,
                                    "instructions": RECALL_INSTRUCTIONS, "max_tokens": LATENT_MAX_TOKENS})
        r.raise_for_status()
        data = r.json() or {}
    note = str(data.get("note") or "").strip()
    if _NOTHING.match(note):
        note = ""
    return {"note": note[:LATENT_MAX_CHARS], "documents": len(docs),
            "server_ms": data.get("ms"), "model": data.get("model")}
