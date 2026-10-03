"""The UDR prompt plans before it queries, knows today's date, and every tool takes a reasoning."""
from __future__ import annotations

from datetime import datetime, timezone

from src.agent.prompts import build_system_prompt
from src.agent.tools.definitions import TOOL_DEFINITIONS


def test_the_prompt_plans_first_and_carries_the_date():
    p = build_system_prompt(datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert "<instructions>" in p and "</instructions>" in p
    assert "Devise your own strategic plan" in p and "Only execute the final query" in p
    assert "Today is 2026-10-01 (Thursday)" in p
    # The JSON contract the orchestrator parses is unchanged, braces and all.
    assert '"summary": "one sentence describing what was found"' in p and '{ ...fields... }' in p
    assert "{today}" not in p


def test_every_tool_requires_a_reasoning_first():
    for t in TOOL_DEFINITIONS:
        schema = t["input_schema"]
        assert schema["properties"]["reasoning"]["type"] == "string", t["name"]
        assert schema["required"][0] == "reasoning", t["name"]
        assert schema["required"].count("reasoning") == 1
