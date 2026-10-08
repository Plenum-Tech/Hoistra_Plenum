"""Every engine's answer streams as it is written, the way compliance's always has.

Compliance streamed its analyst's zones token by token from the start. Every other engine —
energy, maintenance, vendors, the general loop — gathered its rows in silence and delivered
the answer whole at the end, a minute later. The sub-agent runner now streams LangGraph's
`messages` mode alongside `values`, and each AI text chunk becomes an `answer_delta` event on
the same channel the tool events use.

Two things must NOT stream: a chunk that is part of a tool call (the model deciding what to
fetch, not what to say), and compliance's own sub-agent prose (the analyst rewrites it, and a
draft that disagrees with the final would put two answers on one screen).
"""
from langchain_core.messages import AIMessageChunk, ToolMessage

from src.agents.meta_tools import answer_delta_event


class TestWhatBecomesADelta:

    def test_a_text_chunk_streams(self):
        ev = answer_delta_event((AIMessageChunk(content="Kingsway House "), {}), "energy_intelligence")
        assert ev == {"type": "answer_delta", "text": "Kingsway House ", "domain": "energy_intelligence"}

    def test_anthropic_style_content_parts_are_joined(self):
        chunk = AIMessageChunk(content=[{"type": "text", "text": "Four "}, {"type": "text", "text": "buildings"}])
        ev = answer_delta_event((chunk, {}), "wo_engine")
        assert ev["text"] == "Four buildings"

    def test_a_tool_call_chunk_is_not_an_answer(self):
        chunk = AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "list_maintenance_decisions", "args": '{"limit"', "id": "c1", "index": 0}],
        )
        assert answer_delta_event((chunk, {}), "wo_engine") is None

    def test_a_tool_result_is_not_an_answer(self):
        assert answer_delta_event((ToolMessage(content="{...}", tool_call_id="c1"), {}), "wo_engine") is None

    def test_an_empty_chunk_streams_nothing(self):
        assert answer_delta_event((AIMessageChunk(content=""), {}), "x") is None

    def test_garbage_streams_nothing(self):
        assert answer_delta_event(None, "x") is None
        assert answer_delta_event(("not a message", {}), "x") is None


class TestComplianceKeepsItsOwnStream:

    def test_the_engine_wrapper_drops_compliance_drafts_and_keeps_the_rest(self):
        """The filter lives inside _invoke_phase2_engine's `_live` closure; this pins the rule
        it applies by reading the source, since the closure is not importable on its own."""
        import inspect
        from src.agents import orchestrator as o
        src = inspect.getsource(o.DeepAgentOrchestrator._invoke_phase2_engine)
        assert 'engine == "compliance" and event.get("type") == "answer_delta"' in src

    def test_the_general_loop_streams_only_its_own_model_run(self):
        import inspect
        from src.agents import orchestrator as o
        src = inspect.getsource(o.DeepAgentOrchestrator.stream)
        assert 'kind == "on_chat_model_stream"' in src
        assert 'parent_ids' in src
