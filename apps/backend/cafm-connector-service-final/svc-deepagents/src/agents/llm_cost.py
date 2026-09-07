"""Per-turn ledger of model calls: tokens, latency and cost, by role.

Every model call in a compliance turn already returns its token usage; until now nobody read
it. A 75-second answer was a single number with no split — was it the analyst, the reviewer,
a revision? — and a model or effort change could not be judged on cost at all. This module
records one entry per call and one summary per turn, and the pipeline panel in the UI shows
both. It counts and multiplies. Which model to use and at what effort remain decisions made
elsewhere, on the evidence this produces.

Prices are dollars per million tokens and live in ``PRICES``; ``LLM_PRICES_JSON`` in the
environment overrides or extends them without a deploy. An unknown model is still counted —
tokens and time are recorded, cost is ``None`` — because a guessed price is worse than none.
"""
from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ($ per MTok) input, output, cache read, cache write. Cache columns apply to Anthropic models.
PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60, "cache_read": 0.075, "cache_write": 0.0},
    # OpenAI standard tier, short context (the long-context tier starts far above the
    # 30-50K tokens a compliance turn sends). From the published pricing page, Sept 2026.
    "gpt-5.6-sol": {"input": 4.00, "output": 20.00, "cache_read": 0.40, "cache_write": 5.00},
    "gpt-5.6-terra": {"input": 2.00, "output": 12.00, "cache_read": 0.20, "cache_write": 2.50},
    "gpt-5.6-luna": {"input": 0.20, "output": 1.20, "cache_read": 0.02, "cache_write": 0.25},
    "gpt-5.4": {"input": 2.50, "output": 15.00, "cache_read": 0.25, "cache_write": 0.0},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.50, "cache_read": 0.075, "cache_write": 0.0},
    "gpt-5.4-nano": {"input": 0.20, "output": 1.25, "cache_read": 0.02, "cache_write": 0.0},
    "gpt-5.1": {"input": 1.25, "output": 10.00, "cache_read": 0.125, "cache_write": 0.0},
    "gpt-5-mini": {"input": 0.25, "output": 2.00, "cache_read": 0.025, "cache_write": 0.0},
    "gpt-4.1": {"input": 2.00, "output": 8.00, "cache_read": 0.50, "cache_write": 0.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60, "cache_read": 0.10, "cache_write": 0.0},
    "gpt-4o": {"input": 2.50, "output": 10.00, "cache_read": 1.25, "cache_write": 0.0},
}


def _prices() -> dict[str, dict[str, float]]:
    raw = os.getenv("LLM_PRICES_JSON", "").strip()
    if not raw:
        return PRICES
    try:
        extra = json.loads(raw)
        merged = dict(PRICES)
        merged.update({str(k): dict(v) for k, v in extra.items() if isinstance(v, dict)})
        return merged
    except Exception as exc:  # noqa: BLE001 — a bad override must not break a turn
        log.warning("llm_cost.bad_price_override", error=str(exc)[:120])
        return PRICES


def price_usd(model: str, usage: dict[str, int]) -> float | None:
    p = _prices().get(str(model or "").strip())
    if not p:
        return None
    return round(
        usage.get("input_tokens", 0) * p.get("input", 0) / 1e6
        + usage.get("output_tokens", 0) * p.get("output", 0) / 1e6
        + usage.get("cache_read", 0) * p.get("cache_read", 0) / 1e6
        + usage.get("cache_write", 0) * p.get("cache_write", 0) / 1e6,
        6,
    )


def usage_from_anthropic(u: Any) -> dict[str, int]:
    """Normalise an Anthropic ``message.usage`` object.

    ``input_tokens`` on that object excludes cached tokens; the two cache fields are separate.
    """
    g = lambda k: int(getattr(u, k, None) or 0)  # noqa: E731
    return {
        "input_tokens": g("input_tokens"),
        "output_tokens": g("output_tokens"),
        "cache_read": g("cache_read_input_tokens"),
        "cache_write": g("cache_creation_input_tokens"),
    }


def usage_from_langchain_messages(messages: list[Any]) -> tuple[dict[str, int], int]:
    """Sum token usage over the AI messages of a LangGraph run. Returns (usage, llm_steps)."""
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read": 0, "cache_write": 0}
    steps = 0
    for m in messages or []:
        um = getattr(m, "usage_metadata", None)
        if not um:
            meta = getattr(m, "response_metadata", None) or {}
            if not isinstance(meta, dict):
                meta = {}
            # Chat Completions shape ...
            tu = meta.get("token_usage")
            if isinstance(tu, dict):
                um = {
                    "input_tokens": tu.get("prompt_tokens", 0),
                    "output_tokens": tu.get("completion_tokens", 0),
                    "input_token_details": {
                        "cache_read": (tu.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                    },
                }
            # ... and the Responses API shape some newer models return instead.
            ru = meta.get("usage")
            if not um and isinstance(ru, dict):
                um = {
                    "input_tokens": ru.get("input_tokens", ru.get("prompt_tokens", 0)),
                    "output_tokens": ru.get("output_tokens", ru.get("completion_tokens", 0)),
                    "input_token_details": {
                        "cache_read": (ru.get("input_tokens_details") or {}).get("cached_tokens", 0)
                    },
                }
        if not um:
            continue
        steps += 1
        total["input_tokens"] += int(um.get("input_tokens") or 0)
        total["output_tokens"] += int(um.get("output_tokens") or 0)
        details = um.get("input_token_details") if isinstance(um, dict) else None
        cached = int(details.get("cache_read") or 0) if isinstance(details, dict) else 0
        # OpenAI counts cached tokens INSIDE input_tokens; Anthropic reports them separately.
        # Normalise to the Anthropic convention so pricing charges each token once.
        total["cache_read"] += cached
        total["input_tokens"] -= min(cached, int(um.get("input_tokens") or 0))
    return total, steps


@dataclass
class Ledger:
    session_id: str
    started: float = field(default_factory=time.perf_counter)
    entries: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self, role: str, model: str, usage: dict[str, int], ms: float, **extra: Any
    ) -> dict[str, Any]:
        usd = price_usd(model, usage)
        entry: dict[str, Any] = {
            "role": role,
            "model": model,
            **usage,
            "ms": int(ms),
            "usd": usd,
            "cache_hit": bool(usage.get("cache_read")),
            **{k: v for k, v in extra.items() if v is not None},
        }
        self.entries.append(entry)
        log.info("llm.call", session_id=self.session_id, **{k: v for k, v in entry.items() if k != "tools" or v})
        return entry

    def last(self, role: str) -> dict[str, Any] | None:
        for e in reversed(self.entries):
            if e.get("role") == role:
                return e
        return None

    def summary(self) -> dict[str, Any]:
        by_role: dict[str, float] = {}
        known = True
        for e in self.entries:
            if e.get("usd") is None:
                known = False
                continue
            by_role[e["role"]] = round(by_role.get(e["role"], 0.0) + float(e["usd"]), 6)
        return {
            "calls": len(self.entries),
            "usd": round(sum(by_role.values()), 4) if self.entries else 0.0,
            "usd_complete": known,
            "input_tokens": sum(int(e.get("input_tokens", 0)) for e in self.entries),
            "output_tokens": sum(int(e.get("output_tokens", 0)) for e in self.entries),
            "cache_read": sum(int(e.get("cache_read", 0)) for e in self.entries),
            "wall_ms": int((time.perf_counter() - self.started) * 1000),
            "by_role": by_role,
            "models": sorted({str(e.get("model")) for e in self.entries}),
        }

    def log_summary(self, question: str = "") -> dict[str, Any]:
        s = self.summary()
        log.info("llm.turn_cost", session_id=self.session_id, question=question[:120], **s)
        return s


_ledger: ContextVar[Ledger | None] = ContextVar("cafm_llm_ledger", default=None)


def begin_turn(session_id: str) -> Ledger:
    """Start a fresh ledger for this turn. Tasks created afterwards inherit it."""
    ledger = Ledger(session_id=session_id)
    _ledger.set(ledger)
    return ledger


def current() -> Ledger | None:
    return _ledger.get()


def record(role: str, model: str, usage: dict[str, int], ms: float, **extra: Any) -> dict[str, Any] | None:
    """Record on the current turn's ledger; log-only when no turn is active."""
    ledger = _ledger.get()
    if ledger is None:
        entry = {"role": role, "model": model, **usage, "ms": int(ms), "usd": price_usd(model, usage)}
        log.info("llm.call", session_id=None, **entry)
        return entry
    return ledger.record(role, model, usage, ms, **extra)


class Timer:
    """``with Timer() as t: ...`` then ``t.ms``."""

    def __enter__(self) -> "Timer":
        self._t0 = time.perf_counter()
        self.ms = 0.0
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = (time.perf_counter() - self._t0) * 1000
