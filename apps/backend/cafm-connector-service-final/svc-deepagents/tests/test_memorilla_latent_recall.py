"""Memorilla's latent memory as a recall step (services/memorilla.py recall_note,
services/chat_memories.py latent_recall, 23 Nov 2026).

Recall used the Memorilla server only as a similarity search. Latent recall asks it to read a whole set
of memories into its memory tokens and write a short note. It sends only the memories the caller may
see (visible()), because a note blends what it reads and cannot be filtered afterwards; off unless
PLENUM_MEMORY_LATENT is shadow or live; shadow never makes the turn wait; a failure never breaks one.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from src.agents import trace
from src.services import chat_memories as cm
from src.services import memorilla
from test_memorilla_recall import APEX, ASBESTOS, _mem, db  # noqa: F401 - db is a fixture

PREF = "Show costs in pounds, not dirhams."


@pytest.fixture
def server(monkeypatch):
    """The plenum-memory server's recall endpoint, recorded."""
    calls = {"posts": [], "reply": {"note": "Apex Mechanical must not be instructed for AHU-7.", "ms": 840.0,
                                    "model": "memorilla/Memorilla-Qwen3-8B"}, "fail": None}

    class Resp:
        def raise_for_status(self):
            if calls["fail"]:
                raise calls["fail"]

        def json(self):
            return calls["reply"]

    class Client:
        def __init__(self, timeout=None):
            calls["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            calls["posts"].append((url, json))
            return Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    return calls


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    def open_turn():
        trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "turn_id": "turn-l",
                           "session_id": "s1", "summary": "q", "payload": {"message": "q"}})
    return open_turn


def _latent_spans(t):
    return [s for s in t.spans if s.kind == "memory" and s.name == "latent recall"]


# ── the switch ────────────────────────────────────────────────────────────────────────

def test_off_unless_asked_for_and_never_without_the_server(monkeypatch):
    monkeypatch.setenv("PLENUM_MEMORY_URL", "http://127.0.0.1:8000")
    monkeypatch.delenv("PLENUM_MEMORY_LATENT", raising=False)
    assert memorilla.latent_mode() == "off"
    for value, mode in (("shadow", "shadow"), ("LIVE ", "live"), ("on", "off"), ("", "off")):
        monkeypatch.setenv("PLENUM_MEMORY_LATENT", value)
        assert memorilla.latent_mode() == mode
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "live")
    monkeypatch.delenv("PLENUM_MEMORY_URL")
    assert memorilla.latent_mode() == "off"


# ── the API call ──────────────────────────────────────────────────────────────────────

def test_recall_note_posts_the_question_and_only_the_memories_it_is_given(monkeypatch, server):
    monkeypatch.setenv("PLENUM_MEMORY_URL", "http://127.0.0.1:8000/")
    out = asyncio.run(memorilla.recall_note("Can we send Apex to AHU-7?", ["one", " ", "two"]))
    url, body = server["posts"][0]
    assert url == "http://127.0.0.1:8000/v1/memory/recall"
    assert body["question"] == "Can we send Apex to AHU-7?" and body["documents"] == ["one", "two"]
    assert body["instructions"] == memorilla.RECALL_INSTRUCTIONS and body["max_tokens"] == memorilla.LATENT_MAX_TOKENS
    assert out == {"note": "Apex Mechanical must not be instructed for AHU-7.", "documents": 2,
                   "server_ms": 840.0, "model": "memorilla/Memorilla-Qwen3-8B"}
    assert server["timeout"] == memorilla.LATENT_TIMEOUT


@pytest.mark.parametrize("said", ["nothing relevant", "Nothing relevant.", "None", "- no relevant memories"])
def test_nothing_relevant_is_no_note(monkeypatch, server, said):
    monkeypatch.setenv("PLENUM_MEMORY_URL", "http://127.0.0.1:8000")
    server["reply"] = {"note": said}
    assert asyncio.run(memorilla.recall_note("Any rule for AHU-7?", ["x"]))["note"] == ""


def test_a_long_set_is_capped_newest_first_and_no_memories_is_no_call(monkeypatch, server):
    monkeypatch.setenv("PLENUM_MEMORY_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(memorilla, "LATENT_MAX_DOCS", 3)
    asyncio.run(memorilla.recall_note("Any rule for AHU-7?", ["new", "newer-ish", "old", "oldest"]))
    assert server["posts"][0][1]["documents"] == ["new", "newer-ish", "old"]
    assert asyncio.run(memorilla.recall_note("Any rule for AHU-7?", []))["note"] == ""
    assert len(server["posts"]) == 1


# ── what is read, and when the turn waits ────────────────────────────────────────────

def test_latent_recall_sends_exactly_the_memories_the_caller_may_see(db, server, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "live")
    db["mems"] = [_mem("m1", APEX, subject="AHU-7"), _mem("m2", PREF, kind="preference")]
    block = asyncio.run(cm.latent_recall("Can we send Apex to AHU-7?"))
    docs = server["posts"][0][1]["documents"]
    assert docs == [memorilla.memory_text("fact", APEX, "AHU-7"),
                    memorilla.memory_text("preference", PREF, None, personal=True)]
    assert any("FROM plenum_cafm.chat_memories WHERE" in sql for sql, _ in db["sql"]), "read through visible()"
    assert block.startswith("## What earlier conversations add") and "Apex Mechanical must not" in block
    assert "re-read live data" in block


def test_shadow_traces_the_note_and_gives_the_model_nothing(db, server, traced, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "shadow")
    db["mems"] = [_mem("m1", APEX, subject="AHU-7")]

    async def run():
        traced()
        block = await cm.latent_recall("Can we send Apex to AHU-7?")
        return block, trace.current()

    block, t = asyncio.run(run())
    assert block == ""
    span = _latent_spans(t)[0]
    assert span.parent_id == t.root.id and not span.open and span.ok
    assert span.input["mode"] == "shadow" and span.input["visible_memories"] == 1 and span.input["sent"] == 1
    assert span.output["note"].startswith("Apex Mechanical") and span.output["used"] is False


def test_live_marks_the_note_used_in_its_span(db, server, traced, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "live")
    db["mems"] = [_mem("m1", APEX, subject="AHU-7")]

    async def run():
        traced()
        await cm.latent_recall("Can we send Apex to AHU-7?")
        return trace.current()

    assert _latent_spans(asyncio.run(run()))[0].output["used"] is True


def test_a_server_failure_is_traced_and_the_turn_goes_on(db, server, traced, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "live")
    db["mems"] = [_mem("m1", ASBESTOS, subject="B-101")]
    server["fail"] = RuntimeError("502 Bad Gateway")

    async def run():
        traced()
        block = await cm.latent_recall("Plan a lighting upgrade at B-101.")
        return block, trace.current()

    block, t = asyncio.run(run())
    span = _latent_spans(t)[0]
    assert block == "" and span.ok is False and "502" in span.error


def test_no_visible_memories_means_no_server_call(db, server, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "live")
    db["mems"] = []
    assert asyncio.run(cm.latent_recall("Any rule for AHU-7?")) == "" and server["posts"] == []


def test_live_hands_back_a_task_to_await_and_shadow_runs_on_without_one(db, server, monkeypatch):  # noqa: F811
    db["mems"] = [_mem("m1", APEX, subject="AHU-7")]

    async def run(mode):
        monkeypatch.setenv("PLENUM_MEMORY_LATENT", mode)
        task = cm.start_latent_recall("Can we send Apex to AHU-7?")
        got = await task if task is not None else None
        await asyncio.gather(*list(cm._LATENT_TASKS))          # let a shadow recall finish
        return task, got

    task, got = asyncio.run(run("live"))
    assert task is not None and "Apex Mechanical" in got
    posts = len(server["posts"])
    task, got = asyncio.run(run("shadow"))
    assert task is None and len(server["posts"]) == posts + 1, "shadow still asks the server, the turn just does not wait"
    monkeypatch.setenv("PLENUM_MEMORY_LATENT", "off")
    assert cm.start_latent_recall("Can we send Apex to AHU-7?") is None and len(server["posts"]) == posts + 1


def test_both_places_the_orchestrator_builds_the_recall_block_start_it():
    from src.agents import orchestrator

    src = inspect.getsource(orchestrator)
    assert src.count("chat_memories.start_latent_recall(user_message)") == 2
    assert src.count("await _latent_task") == 2
