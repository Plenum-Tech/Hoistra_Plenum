"""Two tools fired at once, and one answer filed under both names.

    TOOL: task  {"agent": "energy_intelligence"}   output: null
    TOOL: task  {"agent": "udr"}                   output: "50 readings … 923.92 kWh"

The energy sub-agent was never failing. The logs show both agents running and both logging
llm.call — energy finishing in 5.1s with 199 output tokens, udr in 17.8s with 1,265. Neither
was cancelled and neither errored. The answer was lost between the graph and the response.

_extract_tool_calls appended one entry per tool call and then attached each result to
`tool_calls[-1]`. With one call in flight that is the right entry by luck. With two — the
orchestrator fires them in parallel on purpose — both are appended empty, the FIRST result
back is written onto the LAST entry, and the second is dropped because that entry already
has one.

So the faster agent's answer was filed under the slower agent's name. Energy returned in 5s
and udr in 18s, which is why it always looked like energy that had failed.

A missing output reads as "this source said nothing". A misattributed one reads as "this
source said that", and nothing downstream can tell the difference.
"""
from __future__ import annotations

from src.agents.orchestrator import _extract_tool_calls


class AI:
    """An assistant message carrying tool calls."""

    def __init__(self, *calls: dict):
        self.content = ""
        self.tool_calls = list(calls)


class ToolMsg:
    """A ToolMessage: a result carrying the id of the call it answers."""

    type = "tool"

    def __init__(self, tool_call_id: str, content):
        self.tool_call_id = tool_call_id
        self.content = content
        self.tool_calls = None


def call(name, args, id_):
    return {"name": name, "args": args, "id": id_}


ENERGY = call("task", {"agent": "energy_intelligence"}, "call_energy")
UDR = call("task", {"agent": "udr"}, "call_udr")


def test_two_parallel_calls_each_keep_their_own_answer():
    """The regression, in the order it actually happened.

    Both calls are issued in one assistant message; energy answers first because it is
    faster. Before the fix that first answer landed on the udr entry and energy showed null.
    """
    out = _extract_tool_calls([
        AI(ENERGY, UDR),
        ToolMsg("call_energy", "energy: 50 readings"),
        ToolMsg("call_udr", "udr: 923.92 kWh"),
    ])
    assert [c["input"]["agent"] for c in out] == ["energy_intelligence", "udr"]
    assert out[0]["output"] == "energy: 50 readings"
    assert out[1]["output"] == "udr: 923.92 kWh"


def test_the_order_results_come_back_in_does_not_matter():
    # The whole point of pairing by id. udr answering first must not move its answer.
    out = _extract_tool_calls([
        AI(ENERGY, UDR),
        ToolMsg("call_udr", "udr: 923.92 kWh"),
        ToolMsg("call_energy", "energy: 50 readings"),
    ])
    assert out[0]["output"] == "energy: 50 readings"
    assert out[1]["output"] == "udr: 923.92 kWh"


def test_a_single_call_still_works():
    out = _extract_tool_calls([AI(UDR), ToolMsg("call_udr", "923.92")])
    assert len(out) == 1 and out[0]["output"] == "923.92"


def test_a_call_with_no_result_says_nothing_rather_than_null():
    # Absent, not None. "No answer yet" and "answered with nothing" are different facts, and
    # a null in the evidence reads to the model as the second.
    out = _extract_tool_calls([AI(ENERGY, UDR), ToolMsg("call_udr", "923.92")])
    assert "output" not in out[0]
    assert out[1]["output"] == "923.92"


def test_an_answer_is_never_attached_to_a_call_it_does_not_belong_to():
    # A stray result whose id matches nothing must not be adopted by whatever came last.
    out = _extract_tool_calls([AI(ENERGY), ToolMsg("call_from_another_turn", "not mine")])
    assert "output" not in out[0]


def test_three_parallel_calls_all_keep_their_own():
    third = call("task", {"agent": "compliance"}, "call_comp")
    out = _extract_tool_calls([
        AI(ENERGY, UDR, third),
        ToolMsg("call_comp", "C"),
        ToolMsg("call_energy", "E"),
        ToolMsg("call_udr", "U"),
    ])
    assert [c["output"] for c in out] == ["E", "U", "C"]


def test_calls_across_several_turns_stay_separate():
    out = _extract_tool_calls([
        AI(call("select_skill", {}, "s1")),
        ToolMsg("s1", "routing"),
        AI(ENERGY, UDR),
        ToolMsg("call_energy", "E"),
        ToolMsg("call_udr", "U"),
    ])
    assert [c["tool"] for c in out] == ["select_skill", "task", "task"]
    assert [c["output"] for c in out] == ["routing", "E", "U"]


def test_an_empty_history_is_no_calls():
    assert _extract_tool_calls([]) == []


def test_a_result_of_empty_string_is_preserved_as_itself():
    # Distinct from absent: the tool answered, and what it said was nothing.
    out = _extract_tool_calls([AI(UDR), ToolMsg("call_udr", "")])
    assert out[0]["output"] == ""


def test_input_defaults_to_a_dict_when_the_call_carries_no_args():
    out = _extract_tool_calls([AI({"name": "get_schema", "args": None, "id": "g1"})])
    assert out[0]["input"] == {}
