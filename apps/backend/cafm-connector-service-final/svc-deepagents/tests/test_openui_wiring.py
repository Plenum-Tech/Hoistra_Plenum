"""Where the orchestrator's OpenUI answers meet the rest of the service.

agents/openui_view.py reads a program and writes its Markdown rendition; these pin the places
that use it: the system prompt teaches the format (and stops when switched off), a thread
started before the change is taught it on every call without its stored history being
rewritten, the loop's answer leaves as Markdown with the program beside it, a later rewrite of
the answer drops a view that no longer matches, and a stored turn keeps the view for a reopened
chat without crowding out the dashboard cards.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents import openui_view as ov
from src.agents import orchestrator as orchestrator_module
from src.agents.context_budget import make_hook
from src.agents.orchestrator import DeepAgentOrchestrator
from src.agents.session_workspace import last_working_tool
from src.agents.system_prompt import build_system_prompt
from src.services import chat_threads

CASES = json.loads((Path(__file__).parent / "fixtures" / "openui" / "cases.json").read_text())
PROGRAM = next(c["program"] for c in CASES if c["name"] == "example-dashboard")
QUESTION = "What needs my attention this week?"


# ── the system prompt ────────────────────────────────────────────────────────────────────────
# Only a chat turn (the WebSocket stream the chat uses, and a chat re-run) is taught the format:
# report cards, /run and the Skill lab call the same loop over REST and read `answer` as text.

@contextmanager
def chat_turn(on: bool = True):
    token = ov.CHAT_TURN.set(on)
    try:
        yield
    finally:
        ov.CHAT_TURN.reset(token)


def test_the_prompt_teaches_the_answer_format_after_everything_else(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    with chat_turn():
        prompt = build_system_prompt("Role: facilities manager")
    assert ov.MARKER in prompt
    assert prompt.index("# Runtime Context") < prompt.index(ov.MARKER)
    assert prompt.endswith(ov.prompt_section())


def test_a_turn_that_is_not_the_chats_is_not_taught_it(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    assert ov.MARKER not in build_system_prompt("Role: facilities manager")


def test_switched_off_the_prompt_does_not_mention_it(monkeypatch):
    monkeypatch.setenv("OPENUI_ANSWERS", "0")
    with chat_turn():
        assert ov.MARKER not in build_system_prompt("Role: facilities manager")


def test_the_chat_stream_and_chat_reruns_mark_their_turns():
    src = (Path(__file__).parents[1] / "src" / "agents" / "orchestrator.py").read_text()
    stream = src[src.index("    async def stream("):]
    rerun = src[src.index("    async def rerun_turn("):]
    assert "openui_view.CHAT_TURN.set(True)" in stream[:6000]
    assert "openui_view.CHAT_TURN.set(True)" in rerun[:6000]


# ── threads started before the change ───────────────────────────────────────────────────────

def sent(messages, agent="orchestrator", chat=True):
    with chat_turn(chat):
        out = make_hook(agent=agent, trim_max_tokens=100_000)({"messages": messages})
    return out.get("llm_input_messages") or messages


def test_a_taught_thread_used_outside_the_chat_is_not_taught_for_that_call(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    taught = SystemMessage(content="PROMPT" + ov.answer_format_block())
    assert sent([taught, HumanMessage(content="hi")], chat=False)[0].content == "PROMPT"


def test_an_old_thread_is_taught_the_format_for_this_call_only(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    stored = [SystemMessage(content="OLD PROMPT"), HumanMessage(content="hi")]
    first = sent(stored)[0]
    assert first.content.startswith("OLD PROMPT") and ov.MARKER in first.content
    assert stored[0].content == "OLD PROMPT"  # the checkpointer's copy is not rewritten


def test_a_thread_that_already_has_it_is_left_alone(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    current = SystemMessage(content="PROMPT" + ov.answer_format_block())
    assert sent([current, HumanMessage(content="hi")])[0].content == current.content


def test_switched_off_a_taught_thread_forgets_it(monkeypatch):
    monkeypatch.setenv("OPENUI_ANSWERS", "0")
    taught = SystemMessage(content="PROMPT" + ov.answer_format_block())
    assert sent([taught, HumanMessage(content="hi")])[0].content == "PROMPT"


def test_sub_agents_are_never_taught_it(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    assert sent([SystemMessage(content="SUB PROMPT"), HumanMessage(content="hi")], agent="udr")[0].content == "SUB PROMPT"


# ── the loop's answer ────────────────────────────────────────────────────────────────────────

class _Agent:
    def __init__(self, text: str):
        self.text = text

    async def ainvoke(self, input_, config):
        return {"messages": [HumanMessage(content=QUESTION), AIMessage(content=self.text)]}


async def run_loop(text: str) -> dict:
    orch = DeepAgentOrchestrator.__new__(DeepAgentOrchestrator)  # no model: the agent is a stub
    orch._agent = _Agent(text)
    orch._llm = None
    return await orch._invoke({"messages": [HumanMessage(content=QUESTION)]}, "t-1", "s-1", record_turn=False)


def views(out: dict) -> list[dict]:
    return [t for t in out["tool_calls"] if t.get("tool") == "openui_view"]


async def test_the_loop_answer_leaves_as_markdown_with_the_program_beside_it(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    out = await run_loop(PROGRAM)
    assert out["answer"] == ov.split_answer(PROGRAM)[0]
    assert "root =" not in out["answer"]
    assert views(out) == [ov.payload_entry({"v": 1, "lang": PROGRAM.strip()})]
    assert out["route_metadata"]["tool"] != "openui_view"


async def test_a_markdown_answer_is_untouched_and_has_no_view(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    out = await run_loop("Seven work orders are overdue.")
    assert out["answer"] == "Seven work orders are overdue."
    assert views(out) == []


async def test_an_answer_the_evaluator_rewrites_loses_its_view(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    monkeypatch.setattr(orchestrator_module, "has_udr_tool_calls", lambda calls: True)

    async def rewrite(**kw):
        return "Six work orders are overdue, not seven.", {}

    monkeypatch.setattr(orchestrator_module, "evaluate_udr_response", rewrite)
    out = await run_loop(PROGRAM)
    assert out["answer"] == "Six work orders are overdue, not seven."
    assert views(out) == []


async def test_a_note_the_evaluator_appends_rides_under_the_view(monkeypatch):
    monkeypatch.delenv("OPENUI_ANSWERS", raising=False)
    monkeypatch.setattr(orchestrator_module, "has_udr_tool_calls", lambda calls: True)

    async def note(**kw):
        return kw["answer"] + "\n\n_Check the figures before quoting them._", {}

    monkeypatch.setattr(orchestrator_module, "evaluate_udr_response", note)
    out = await run_loop(PROGRAM)
    assert views(out) == [ov.payload_entry({"v": 1, "lang": PROGRAM.strip(), "tail": "_Check the figures before quoting them._"})]


async def test_switched_off_no_view_is_sent(monkeypatch):
    monkeypatch.setenv("OPENUI_ANSWERS", "0")
    out = await run_loop(PROGRAM)
    assert "root =" not in out["answer"]
    assert views(out) == []


def test_the_view_never_decides_the_turns_domain():
    calls = [{"tool": "list_work_orders"}, {"tool": "compliance_response"}, {"tool": "openui_view"}]
    assert last_working_tool(calls) == "list_work_orders"


# ── the stored turn ──────────────────────────────────────────────────────────────────────────

CARDS = {"tool": "compliance_response", "output": {"narrative": "Seven are overdue."}}


def test_a_stored_turn_keeps_the_view_beside_the_cards():
    kept = chat_threads._card_payloads([{"tool": "list_work_orders", "output": {}}, CARDS, ov.payload_entry({"v": 1, "lang": "p"})])
    assert {"tool": "compliance_response", "output": CARDS["output"]} in kept
    assert {"tool": "openui_view", "output": {"v": 1, "lang": "p"}} in kept


def test_an_oversized_view_is_not_stored_and_the_cards_still_are():
    big = ov.payload_entry({"v": 1, "lang": "x" * (chat_threads.OPENUI_MAX_BYTES + 10)})
    assert [k["tool"] for k in chat_threads._card_payloads([CARDS, big])] == ["compliance_response"]


def test_cards_too_big_to_store_do_not_take_the_view_with_them():
    huge = {"tool": "compliance_response", "output": {"narrative": "x" * (chat_threads.CARD_MAX_BYTES + 10)}}
    assert [k["tool"] for k in chat_threads._card_payloads([huge, ov.payload_entry({"v": 1, "lang": "p"})])] == ["openui_view"]
