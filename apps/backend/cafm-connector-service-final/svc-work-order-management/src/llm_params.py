"""The parameters a chat-completions call may send, for the model it is sent to.

Creating a work order from the chat failed on 28 Sep 2026 with "the maintenance service
returned an internal error": every call in this service sent `max_tokens`, and the configured
model (OPENAI_MODEL=gpt-5.6) rejects it outright - 400 "Unsupported parameter: 'max_tokens' is
not supported with this model. Use 'max_completion_tokens' instead." The same family accepts
only the default temperature, and the gpt-5.6 models refuse function tools unless told
reasoning_effort="none". The rules are svc-deepagents' (src/llm_factory.py), measured there
against the live endpoint; they are repeated here because this service does not import that one.
"""
from __future__ import annotations

from typing import Any

#: Families that renamed the output ceiling to max_completion_tokens and take only the
#: default temperature.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")
#: The one family that needs reasoning_effort="none" (with tools it is required; without, it
#: stops a reasoning pass spending the whole ceiling before any output). gpt-5 and gpt-5-mini
#: reject "none" and take "minimal"; o4-mini takes neither, so the field is left out for it.
_NONE_PREFIXES = ("gpt-5.6",)
_MINIMAL_PREFIXES = ("gpt-5-mini", "gpt-5-nano")


def _name(model: str | None) -> str:
    return (model or "").strip().lower()


def completion_kwargs(model: str | None, limit: int, *, temperature: float | None = None) -> dict[str, Any]:
    """Keyword arguments for client.chat.completions.create(model=..., **these)."""
    name = _name(model)
    reasoning = name.startswith(_REASONING_PREFIXES)
    out: dict[str, Any] = {("max_completion_tokens" if reasoning else "max_tokens"): limit}
    if temperature is not None and not reasoning:
        out["temperature"] = temperature
    if name.startswith(_NONE_PREFIXES):
        out["reasoning_effort"] = "none"
    elif name.startswith(_MINIMAL_PREFIXES) or name == "gpt-5":
        out["reasoning_effort"] = "minimal"
    return out
