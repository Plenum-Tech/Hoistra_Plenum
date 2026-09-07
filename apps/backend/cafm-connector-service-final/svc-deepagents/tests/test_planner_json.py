"""The planner's JSON must survive the ways models malform it.

A parse failure silently drops every filter and re-reads the whole register: the answer
still arrives, so nothing looks broken, but the query behind it is gone and the analyst is
handed the entire table. That is exactly how "blocked vendors whose certificate is
compliant" ended up reasoning over 16 rows instead of 2.
"""

import pytest

from src.agents.orchestrator import DeepAgentOrchestrator as _O

_loads = _O._loads_lenient


def test_clean_json_is_unchanged():
    assert _loads('{"reason":"a","needs":[],"sub_questions":[{"id":"q1"}]}') == {
        "reason": "a",
        "needs": [],
        "sub_questions": [{"id": "q1"}],
    }


def test_prose_either_side_is_discarded():
    assert _loads('Here is the plan:\n{"reason":"a"}\nhope that helps') == {"reason": "a"}


def test_trailing_commas_are_repaired():
    assert _loads('{"reason":"a","needs":["coverage",],}') == {
        "reason": "a",
        "needs": ["coverage"],
    }


def test_single_quotes_are_repaired():
    assert _loads("{'reason':'a','needs':[]}") == {"reason": "a", "needs": []}


def test_truncation_closes_innermost_first():
    """Counting braces and brackets separately closes nesting in the wrong order."""
    out = _loads(
        '{"reason":"a","sub_questions":[{"id":"q1","query":{"filters":[{"field":"status"'
    )
    assert out["sub_questions"][0]["query"]["filters"][0]["field"] == "status"


def test_truncation_mid_string_is_closed():
    assert _loads('{"reason":"gather blocked ven') == {"reason": "gather blocked ven"}


def test_truncation_after_a_key_drops_the_dangling_key():
    out = _loads('{"reason":"a","sub_questions":[{"id":"q1","text":"x","query":')
    assert out["sub_questions"][0] == {"id": "q1", "text": "x"}


def test_unrecoverable_input_still_raises():
    with pytest.raises(ValueError):
        _loads("not json at all")


@pytest.mark.asyncio
async def test_planner_degrades_when_the_model_call_itself_raises():
    """A 400, a bad model name or a timeout must not take the turn down with it.

    The failure handler logs the raw response, so `raw` has to be bound before the call is
    made — otherwise an UnboundLocalError replaces a recoverable planning failure with an
    exception, and the whole compliance turn dies instead of falling back to a full read.
    """
    from src.agents.orchestrator import DeepAgentOrchestrator

    class _Boom:
        async def ainvoke(self, _messages):
            raise RuntimeError("Error code: 400 - unsupported reasoning_effort")

    orch = DeepAgentOrchestrator.__new__(DeepAgentOrchestrator)
    orch._llm = _Boom()
    DeepAgentOrchestrator._cert_vocab_cache = {}  # skip the database read

    plan = await orch._plan_compliance_query("which certificates are lapsed?")

    assert plan["reason"]  # fell back to the default plan
    assert plan["sub_questions"][0]["id"] == "q1"
    assert plan["sub_questions"][0]["query"] == {}  # no filters -> caller reads everything
