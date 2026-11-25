"""A record question that arrives while the records are loading waits for the load (25 Nov 2026).

Production traces, 24 Nov, twice: a planned step answer_from_records took ~25 s and then failed
with "The records are still being loaded." on a freshly started process. The service starts
loading the ontology at startup (api/main.py, _warm_ontology); a question that arrives first waits
on that same load, but only ONTOLOGY_WAIT_S - 25 s - and a load from the cache plus the bind
against the database outlasted it. The load finished moments later, for nobody.

So the wait is long enough for a cold load (90 s) and still bounded: a full build from scratch
(~15 minutes) still answers "still loading" and carries on for the next question.

The clock is scaled here - one second of the tool's wait is 10 ms - so the slow loader is slow
in the tool's own terms without the test taking minutes.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time

import pytest

from src.agents import ontology_qa as oq

ANSWER = getattr(oq.answer_from_records, "coroutine", oq.answer_from_records)
SCALE = 0.01                          # one second of the tool's wait, in real seconds


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    from src import database

    def refuse(*a, **k):
        raise AssertionError("an ontology test tried to reach the database")

    monkeypatch.setattr(database, "AsyncSessionLocal", refuse)
    monkeypatch.setattr(database, "_get_session_factory", refuse, raising=False)


@pytest.fixture
def scaled_clock(monkeypatch):
    """asyncio.wait_for with its timeout in scaled seconds."""
    real = asyncio.wait_for

    async def wait_for(aw, timeout):
        return await real(aw, None if timeout is None else timeout * SCALE)

    monkeypatch.setattr(asyncio, "wait_for", wait_for)
    monkeypatch.setattr(oq, "scope_hint", lambda: "", raising=False)


def loader(seconds, done):
    async def load():
        await asyncio.sleep(seconds * SCALE)
        done.append(True)
        return object()
    return load


async def answered(question):
    return {"ok": True, "found": True, "records": [{"key": "v-1"}], "q": question}


def test_by_default_the_wait_outlasts_a_cold_load():
    assert 'os.environ.get("ONTOLOGY_WAIT_S", "90")' in inspect.getsource(oq)
    assert "ONTOLOGY_WAIT_S" not in os.environ, "the deployment sets its own wait"
    assert oq.ONTOLOGY_WAIT_S >= 90


def test_a_question_during_a_30_second_load_waits_and_is_answered(monkeypatch, scaled_clock):
    """The traced case: the load outlasted the old 25 s wait."""
    done = []
    monkeypatch.setattr(oq, "get_ontology", loader(30, done))
    monkeypatch.setattr(oq, "answer_question", answered)
    out = asyncio.run(ANSWER("Which vendors work at Aire Mill Court?"))
    assert done == [True]
    assert out.get("still_loading") is not True, "gave up while the records were still loading"
    assert out["ok"] is True and out["records"] == [{"key": "v-1"}]


def test_it_is_answered_as_soon_as_the_load_finishes_not_at_the_end_of_the_wait(monkeypatch, scaled_clock):
    done = []
    monkeypatch.setattr(oq, "get_ontology", loader(10, done))
    monkeypatch.setattr(oq, "answer_question", answered)
    t0 = time.perf_counter()
    out = asyncio.run(ANSWER("open work orders at Bishopsgate"))
    assert out["ok"] is True
    assert time.perf_counter() - t0 < 60 * SCALE


def test_a_build_from_scratch_is_still_bounded_and_carries_on(monkeypatch, scaled_clock):
    done = []
    monkeypatch.setattr(oq, "get_ontology", loader(130, done))

    async def never(question):
        raise AssertionError("answered before the ontology was ready")

    monkeypatch.setattr(oq, "answer_question", never)

    async def ask():
        t0 = time.perf_counter()
        out = await ANSWER("open work orders at Bishopsgate")
        waited = time.perf_counter() - t0
        await asyncio.sleep(60 * SCALE)
        return out, waited

    out, waited = asyncio.run(ask())
    assert out["still_loading"] is True and out["ok"] is False
    assert waited < 120 * SCALE, "waited %.2fs" % waited
    assert done == [True], "the load is left to finish for the next question"
