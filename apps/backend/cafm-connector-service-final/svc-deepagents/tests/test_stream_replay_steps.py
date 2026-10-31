"""A replayed compliance turn streams the same kinds of event a live one does.

The orchestrator has two streaming paths. The live one (_invoke_phase2_engine) emits the
pipeline's steps as `compliance_step` events while the composer runs, so the run trace rail
shows how the answer was reached. The replay one (_stream_compliance_progressive) hands back
a turn that has already finished, and emitted only tool events and zones — so the same
question produced a trace with the pipeline in it or without it, depending on which path the
orchestrator happened to take, for no reason a reader of the trace could see.

The steps exist either way: they are already in the compliance_pipeline tool call the replay
carries. These pin that the replay emits them too, and that it still carries the finished
turn's tool_calls — which is what the answer's cost ledger and steps card are read from.
"""
from __future__ import annotations

import pytest

from src.agents.orchestrator import DeepAgentOrchestrator

STEPS = [
    {"stage": "plan", "label": "Planned the read", "ms": 1370, "usd": 0.000133},
    {"stage": "fetch", "label": "Fetched 9 rows", "ms": 112},
    {"stage": "analyse", "label": "Wrote the answer", "ms": 46002, "usd": 0.227981},
]
COST = {
    "calls": 2, "usd": 0.2282, "usd_complete": True, "wall_ms": 48767,
    "by_role": {"plan": 0.000229, "analyst": 0.227981},
    "models": ["claude-opus-5", "gpt-4o-mini"],
}
SHORTCUT = {
    "answer": "Six vendors are currently blocked.",
    "tool_calls": [
        {"tool": "list_vendor_accreditations", "input": {}, "output": {"rows": []}},
        {"tool": "compliance_response", "input": {}, "output": {
            "narrative": "Six vendors are currently blocked.", "kpis": [{"label": "Blocked", "value": "6"}]}},
        {"tool": "compliance_pipeline", "input": {}, "output": {"steps": STEPS, "cost": COST}},
    ],
}


async def replay(shortcut=None) -> list[dict]:
    """The events the replay path streams, in order."""
    orch = DeepAgentOrchestrator.__new__(DeepAgentOrchestrator)  # no LLM needed for a replay
    return [e async for e in orch._stream_compliance_progressive(shortcut or SHORTCUT, "s-1")]


async def test_a_replayed_turn_streams_its_pipeline_steps():
    events = await replay()
    steps = [e for e in events if e.get("type") == "compliance_step"]
    assert [s["step"]["label"] for s in steps] == [s["label"] for s in STEPS]


async def test_the_steps_arrive_before_the_turn_is_declared_complete():
    events = await replay()
    kinds = [e.get("type") for e in events]
    assert kinds[-1] == "workflow_completed"
    assert kinds.index("compliance_step") < kinds.index("workflow_completed")


async def test_the_finished_turn_still_carries_its_tool_calls():
    # The answer's cost ledger and steps card are read from these, not from the events.
    completion = (await replay())[-1]
    tools = [t["tool"] for t in completion["tool_calls"]]
    assert "compliance_pipeline" in tools and "compliance_response" in tools


async def test_the_raw_tool_calls_are_still_not_replayed_as_tool_events():
    # compliance_response and compliance_pipeline are carried as zones and steps; emitting
    # them as tool rows too would show the reader the same work twice.
    events = await replay()
    replayed = {e.get("tool") for e in events if e.get("type") == "tool_started"}
    assert replayed == {"list_vendor_accreditations"}


async def test_a_turn_with_no_pipeline_streams_no_steps():
    thin = {"answer": "ok", "tool_calls": [
        {"tool": "list_vendor_accreditations", "input": {}, "output": {"rows": []}}]}
    events = await replay(thin)
    assert not [e for e in events if e.get("type") == "compliance_step"]
    assert events[-1]["type"] == "workflow_completed"
