"""Self-managed context: the agent keeps its own working context under a token budget.

Every tool result an agent reads stays in its context until the turn ends, so a question that
reads five registers sends all five, in full, with every later model call. Measured in Hoist
Traces (5 Oct 2026, 14 days): the orchestrator loop and the compliance agent grew 21-24k tokens
inside one question, up to 75k. Trimming the oldest messages - what the loop did before - drops
whole results the model may still need, chosen by age rather than by use.

Here the model chooses. Before every model step a hook (``make_hook``):

1. **labels** each tool result r1, r2 ... in the order it arrived;
2. **applies** any ``compact_context`` call the model made on its last step: the results it
   released are replaced by a one-line stub and its notes stay in their place - but only when the
   notes are shorter than what they replace (the gate), so a compaction can only shrink;
3. **shrinks** mechanically, oldest result first, if the context is still over the hard limit, so
   an overflow costs detail rather than the turn;
4. **notices**: past half the budget, the model's view of the last message carries the usage,
   the largest results it could release with their labels, and the instructions in
   skills/query-builder/context-budget.md (read on every step, so the skill lab can tune it).

The system prompt and the user's question are never touched: only tool results are released.
The notice is added to what the model is SENT on this step, never stored.

This is Hoistra's own implementation of the idea in "Context Language Models" (Shao et al.,
2026). None of that project's code is used; it is licensed for non-commercial use only.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

import structlog
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langchain_core.tools import tool
from langgraph.graph.message import REMOVE_ALL_MESSAGES

log = structlog.get_logger(__name__)

COMPACT_TOOL = "compact_context"
PENDING = "[compaction requested - applied before your next step]"
RELEASED = "[released"
#: Headroom kept free under the budget for the next model output.
RESERVE_TOKENS = 2000
#: Below this a result is not worth a compaction call.
MIN_RELEASABLE_TOKENS = 300
#: The notice starts at this share of the budget, and turns urgent at the second.
NOTICE_AT, URGENT_AT = 0.5, 0.75
#: How much of a result the overflow shrink keeps.
SHRINK_KEEP_CHARS = 1200

#: "self" (compact), "trim" (the old behaviour) or None (follow settings.context_self_manage).
#: The skill lab sets it to compare the two on the same question.
MODE: ContextVar[str | None] = ContextVar("cafm_context_mode", default=None)
#: Counters for the turn, read by the skill lab. None outside a measured turn.
STATS: ContextVar[dict[str, Any] | None] = ContextVar("cafm_context_stats", default=None)


def new_stats() -> dict[str, Any]:
    return {"notices": 0, "compactions": 0, "refused": 0, "shrunk": 0, "tokens_freed": 0,
            "peak_working_tokens": 0}


@tool(COMPACT_TOOL)
def compact_context(notes: str, release: list[str]) -> str:
    """Free your working context: replace tool results you have finished with short notes.

    Use it when the context notice says you are past half your budget. ``release`` lists the
    result labels shown in the notice (for example ["r3", "r5"]). ``notes`` keeps everything you
    still need from those results - every figure, date, code, name and status exactly as the tool
    returned it, with its label - and must be shorter than what it replaces, or nothing changes.
    """
    return PENDING


# ── reading the message list ─────────────────────────────────────────────────────────────

def _tokens(messages: list) -> int:
    try:
        return int(count_tokens_approximately(messages))
    except Exception:  # noqa: BLE001
        return sum(len(str(getattr(m, "content", ""))) // 4 for m in messages)


def working_tokens(messages: list) -> int:
    """Tokens in everything after the system prompt - the part a budget can do anything about."""
    return _tokens([m for m in messages if not isinstance(m, SystemMessage)])


def _call_names(messages: list) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for m in messages:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                out[str(tc.get("id"))] = (str(tc.get("name") or ""), dict(tc.get("args") or {}))
    return out


def label_results(messages: list) -> list[dict[str, Any]]:
    """Every tool result, labelled r1.. in arrival order. Labels never move: results are only
    ever replaced in place, never removed, so r3 is r3 for the whole turn."""
    names = _call_names(messages)
    out = []
    n = 0
    for i, m in enumerate(messages):
        if not isinstance(m, ToolMessage):
            continue
        n += 1
        name = getattr(m, "name", None) or names.get(str(m.tool_call_id), ("", {}))[0]
        content = m.content if isinstance(m.content, str) else str(m.content)
        out.append({"label": f"r{n}", "index": i, "tool_call_id": str(m.tool_call_id), "name": name,
                    "tokens": _tokens([m]),
                    "released": content.startswith(RELEASED),
                    "is_compact": name == COMPACT_TOOL})
    return out


def _replace(messages: list, index: int, content: str) -> None:
    m = messages[index]
    messages[index] = m.model_copy(update={"content": content})


# ── 2. applying the model's compaction ───────────────────────────────────────────────────

def apply_compactions(messages: list) -> tuple[list, list[dict[str, Any]]]:
    """Apply every ``compact_context`` call still pending. Returns the new list (the same object
    when nothing changed) and one event per call: accepted or refused, and why."""
    labels = label_results(messages)
    pending = [r for r in labels if r["is_compact"]
               and str(messages[r["index"]].content) == PENDING]
    if not pending:
        return messages, []
    calls = _call_names(messages)
    out = list(messages)
    events = []
    by_label = {r["label"]: r for r in labels}
    for p in pending:
        _, args = calls.get(p["tool_call_id"], ("", {}))
        notes = str(args.get("notes") or "").strip()
        wanted = [str(x).strip() for x in (args.get("release") or []) if str(x).strip()]
        chosen = [by_label[w] for w in wanted
                  if w in by_label and not by_label[w]["released"] and not by_label[w]["is_compact"]]
        freed = sum(r["tokens"] for r in chosen)
        note_tokens = _tokens([HumanMessage(content=notes)]) if notes else 0
        reason = None
        if not chosen:
            reason = "none of those labels is a result still in context"
        elif not notes:
            reason = "notes are empty; say what you still need from the results first"
        elif note_tokens >= freed:
            reason = f"the notes ({note_tokens} tokens) are not shorter than what they replace ({freed})"
        if reason:
            _replace(out, p["index"], f"Not applied: {reason}. Nothing was released.")
            events.append({"accepted": False, "reason": reason, "label": p["label"]})
            continue
        for r in chosen:
            _replace(out, r["index"], f"{RELEASED} {r['label']} {r['name']}: kept as notes in {p['label']}]")
            r["released"] = True
        _replace(out, p["index"],
                 f"Notes kept in place of {', '.join(r['label'] for r in chosen)} "
                 f"({freed - note_tokens} tokens freed):\n{notes}")
        events.append({"accepted": True, "label": p["label"], "released": [r["label"] for r in chosen],
                       "freed": freed - note_tokens})
    return out, events


# ── 3. the overflow safety net ───────────────────────────────────────────────────────────

def shrink_overflow(messages: list, limit: int) -> tuple[list, int]:
    """Cut the oldest large results to their opening lines until the context fits ``limit``.
    The model's own compaction is the way out; this keeps a turn alive when it did not happen."""
    if working_tokens(messages) <= limit:
        return messages, 0
    out = list(messages)
    shrunk = 0
    for r in label_results(out):
        if working_tokens(out) <= limit:
            break
        if r["released"] or r["is_compact"] or r["tokens"] < MIN_RELEASABLE_TOKENS:
            continue
        content = str(out[r["index"]].content)
        if content.startswith("[cut to fit"):
            continue
        _replace(out, r["index"],
                 f"[cut to fit the context budget: {r['label']} {r['name']} was {r['tokens']} tokens; "
                 f"the opening is kept below. Call the tool again if you need the rest.]\n"
                 + content[:SHRINK_KEEP_CHARS])
        shrunk += 1
    return out, shrunk


# ── 4. the notice ────────────────────────────────────────────────────────────────────────

def instructions() -> str:
    from ..services import skill_overlays
    return skill_overlays.doc("query-builder", "context-budget")


def notice(messages: list, budget: int, *, shrunk: int = 0) -> str | None:
    used = working_tokens(messages)
    ratio = used / budget if budget else 0.0
    if ratio < NOTICE_AT and not shrunk:
        return None
    free = sorted((r for r in label_results(messages)
                   if not r["released"] and not r["is_compact"] and r["tokens"] >= MIN_RELEASABLE_TOKENS),
                  key=lambda r: -r["tokens"])[:8]
    if not free and not shrunk:
        return None
    head = f"[Context: {used:,} of {budget:,} tokens ({round(ratio * 100)}%)."
    if ratio >= URGENT_AT:
        head += " Compact now, before reading anything else."
    if shrunk:
        head += f" {shrunk} result(s) were cut to fit; compact earlier to choose what is kept."
    listing = ", ".join(f"{r['label']} {r['name']} ({r['tokens']:,} tokens)" for r in free)
    body = f" Results you can release: {listing}.]" if listing else "]"
    return head + body + "\n\n" + instructions()


def _with_notice(view: list, text: str) -> list:
    """The notice rides on the last message the model will read, so no provider sees a system
    message in the middle of a conversation or a user turn between a call and its result."""
    if not view:
        return view
    last = view[-1]
    content = last.content if isinstance(last.content, str) else str(last.content)
    out = list(view)
    out[-1] = last.model_copy(update={"content": content + "\n\n" + text})
    return out


# ── the hook ─────────────────────────────────────────────────────────────────────────────

def _self_managing() -> bool:
    mode = MODE.get()
    if mode is not None:
        return mode == "self"
    from ..config import settings
    return bool(getattr(settings, "context_self_manage", True))


def _legacy_trim(messages: list, max_tokens: int | None) -> list | None:
    if not max_tokens:
        return None
    try:
        trimmed = trim_messages(messages, strategy="last", token_counter=count_tokens_approximately,
                                max_tokens=max_tokens, start_on="human", end_on=("human", "tool", "ai"),
                                include_system=True, allow_partial=False)
    except Exception:  # noqa: BLE001
        return None
    return trimmed if trimmed and len(trimmed) < len(messages) else None


def make_hook(*, agent: str, trim_max_tokens: int | None, budget: int | None = None):
    """A ``pre_model_hook`` for create_react_agent. ``trim_max_tokens`` keeps the old trim as the
    last resort for a long multi-turn session, and is the whole behaviour when self-management is
    off."""

    def hook(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages") or []
        if not messages:
            return {}
        if not _self_managing():
            trimmed = _legacy_trim(messages, trim_max_tokens)
            return {"llm_input_messages": trimmed} if trimmed else {}
        from ..config import settings
        limit_budget = int(budget or getattr(settings, "context_budget_tokens", 24000))
        try:
            new, events = apply_compactions(messages)
            new, shrunk = shrink_overflow(new, limit_budget - RESERVE_TOKENS)
        except Exception as exc:  # noqa: BLE001 - the budget must never cost a turn
            log.warning("context.hook_failed", agent=agent, error=str(exc)[:200])
            trimmed = _legacy_trim(messages, trim_max_tokens)
            return {"llm_input_messages": trimmed} if trimmed else {}
        view = list(new)
        text = notice(view, limit_budget, shrunk=shrunk)
        if text:
            view = _with_notice(view, text)
        view = _legacy_trim(view, trim_max_tokens) or view
        _account(agent, events, shrunk, bool(text), working_tokens(new))
        out: dict[str, Any] = {"llm_input_messages": view}
        if new is not messages:
            out["messages"] = [RemoveMessage(id=REMOVE_ALL_MESSAGES), *new]
        return out

    return hook


def _account(agent: str, events: list, shrunk: int, noticed: bool, working: int) -> None:
    stats = STATS.get()
    if stats is not None:
        stats["notices"] += int(noticed)
        stats["compactions"] += sum(1 for e in events if e["accepted"])
        stats["refused"] += sum(1 for e in events if not e["accepted"])
        stats["shrunk"] += shrunk
        stats["tokens_freed"] += sum(int(e.get("freed") or 0) for e in events if e["accepted"])
        stats["peak_working_tokens"] = max(stats["peak_working_tokens"], working)
    for e in events:
        log.info("context.compaction", agent=agent, **e)
    if shrunk:
        log.info("context.shrunk", agent=agent, results=shrunk)
    if events or shrunk:
        try:
            from . import activity_log
            summary = "; ".join(
                (f"released {','.join(e['released'])} ({e['freed']} tokens freed)" if e["accepted"]
                 else f"refused: {e['reason']}") for e in events)
            if shrunk:
                summary = (summary + "; " if summary else "") + f"{shrunk} result(s) cut to fit"
            activity_log.fire(agent=agent, stage="context", direction="output",
                              summary=summary[:400], payload={"events": events, "shrunk": shrunk})
        except Exception:  # noqa: BLE001
            pass
