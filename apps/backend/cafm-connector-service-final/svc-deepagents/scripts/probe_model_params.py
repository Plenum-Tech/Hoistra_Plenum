"""Which chat-completions parameters does a model accept? (23 Nov 2026)

llm_factory.py decides temperature, the token-limit name and reasoning_effort by model-name prefix, from a
table measured against the live endpoint. Run this before pointing OPENAI_MODEL at a new model:

    python scripts/probe_model_params.py gpt-6.1-terra gpt-5.6-terra

It sends a handful of tiny requests (about 20 output tokens each) with OPENAI_API_KEY from the environment,
prints OK / FAIL per case and the first words of each refusal, and never prints the key. It then runs the
model through llm_factory.create_chat_model with a tool bound - the exact way the agents call it.
"""
from __future__ import annotations

import asyncio
import os
import sys

from openai import OpenAI

TOOL = [{"type": "function", "function": {
    "name": "count_buildings", "description": "Count the company's buildings.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}]
MSG = [{"role": "user", "content": "Reply with the single word: ready"}]
TOOL_MSG = [{"role": "user", "content": "How many buildings do we have? Use the tool."}]

CASES = [
    ("plain, max_completion_tokens", dict(messages=MSG, max_completion_tokens=200)),
    ("max_tokens (old name)", dict(messages=MSG, max_tokens=200)),
    ("temperature=0", dict(messages=MSG, max_completion_tokens=200, temperature=0)),
    ("tools, reasoning omitted", dict(messages=TOOL_MSG, tools=TOOL, max_completion_tokens=200)),
    ("tools, reasoning none", dict(messages=TOOL_MSG, tools=TOOL, max_completion_tokens=200, reasoning_effort="none")),
    ("tools, reasoning minimal", dict(messages=TOOL_MSG, tools=TOOL, max_completion_tokens=200, reasoning_effort="minimal")),
    ("tools, reasoning low", dict(messages=TOOL_MSG, tools=TOOL, max_completion_tokens=200, reasoning_effort="low")),
]


def probe(client: OpenAI, model: str) -> None:
    print(f"\n== {model}")
    for label, kw in CASES:
        try:
            r = client.chat.completions.create(model=model, **kw)
            choice = r.choices[0]
            what = ("tool call: " + choice.message.tool_calls[0].function.name) if choice.message.tool_calls \
                else repr((choice.message.content or "")[:40])
            print(f"  OK    {label:<30} {what}  [{r.model}, {r.usage.total_tokens} tokens]")
        except Exception as exc:  # noqa: BLE001 - every refusal is a result here
            text = " ".join(str(getattr(exc, "message", exc)).split())[:150]
            print(f"  FAIL  {label:<30} {type(exc).__name__}: {text}")


async def through_factory(model: str) -> None:
    """The agents' own path: create_chat_model + a bound tool, one call."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from langchain_core.tools import tool

    from src.llm_factory import create_chat_model

    @tool
    def count_buildings() -> int:
        """Count the company's buildings."""
        return 2

    llm = create_chat_model(model=model).bind_tools([count_buildings])
    try:
        out = await llm.ainvoke(TOOL_MSG[0]["content"])
        calls = [c["name"] for c in (out.tool_calls or [])]
        print(f"  OK    llm_factory + bound tool      tool calls: {calls or 'none'}; "
              f"text: {repr((out.content or '')[:40])}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL  llm_factory + bound tool      {type(exc).__name__}: {' '.join(str(exc).split())[:150]}")


def main() -> int:
    models = sys.argv[1:] or [os.environ.get("OPENAI_MODEL", "gpt-6.1-terra")]
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        print("OPENAI_API_KEY is not set in this environment.")
        return 2
    base = (os.environ.get("OPENAI_API_BASE") or os.environ.get("OPENAI_BASE_URL") or "").strip() or None
    client = OpenAI(base_url=base, timeout=60)
    for m in models:
        probe(client, m)
        asyncio.run(through_factory(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
