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

import os
import re
import time
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


async def plan_of_question(org: str | None, question: str) -> tuple[dict[str, Any] | None, str | None]:
    """The plan of the latest good turn that asked exactly this question: (plan, turn_id).

    Good = finished ok, no thumbs-down, and no verify step failed - the same rule the export used."""
    from ..database import _get_engine

    async with _get_engine().connect() as conn:
        row = (await conn.execute(text("""
            SELECT p.output->'plan' AS plan, t.turn_id
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
        return None, None
    plan = row[0]
    if isinstance(plan, str):
        import json

        plan = json.loads(plan)
    return (plan if isinstance(plan, dict) else None), row[1]


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
        plan, turn_id = await plan_of_question(org, past_q)
        info["from_turn"] = turn_id
        if plan is None:
            info["used"], info["why"] = False, "the past turn's plan was not found in the trace tables"
            return None, info
        checked, why = validate({k: v for k, v in plan.items() if k != "areas"})
        if checked is None:
            info["used"], info["why"] = False, f"the past plan no longer validates: {why}"
            return None, info
        info["used"], info["why"] = True, "ok"
        return checked, info
    except Exception as exc:  # noqa: BLE001 - recall never breaks a turn
        info["used"], info["error"] = False, str(exc)[:300]
        log.warning("memorilla.recall_plan_failed", error=str(exc)[:200])
        return None, info
    finally:
        info["ms"] = round((time.perf_counter() - t0) * 1000, 1)
