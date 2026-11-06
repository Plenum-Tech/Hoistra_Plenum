"""A record question does not hang while the ontology is built (2 Nov 2026).

"Show work-order deviations at Bishopsgate Tower this month" routed to the work-order engine, which
called answer_from_records, which waited on the ontology build. Locally against the Azure database
that build is ~15 minutes (one value-profile query per column, one after another), and every
container rebuild threw the cached build away with /tmp. So: the cache can live in a directory that
outlives the container (ONTOLOGY_CACHE_DIR, a Docker volume locally), and the tool waits a bounded
time, then says the map is still loading while the build carries on.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from src.agents import ontology_qa as oq

# The tool as the agents call it is a LangChain tool; its coroutine is the function itself.
ANSWER = getattr(oq.answer_from_records, "coroutine", oq.answer_from_records)


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    from src import database

    def refuse(*a, **k):
        raise AssertionError("an ontology test tried to reach the database")

    monkeypatch.setattr(database, "AsyncSessionLocal", refuse)
    monkeypatch.setattr(database, "_get_session_factory", refuse, raising=False)


def test_the_cache_lives_where_ontology_cache_dir_says(monkeypatch, tmp_path):
    monkeypatch.setenv("ONTOLOGY_CACHE_DIR", str(tmp_path / "onto"))
    path = oq._cache_path("abc123")
    assert path == str(tmp_path / "onto" / "cafm_ontology_abc123.json")
    assert (tmp_path / "onto").is_dir(), "the directory is made when missing"


def test_without_it_the_cache_stays_in_the_temp_directory(monkeypatch):
    import tempfile

    monkeypatch.delenv("ONTOLOGY_CACHE_DIR", raising=False)
    assert oq._cache_path("abc123").startswith(tempfile.gettempdir())


def test_a_record_question_waits_a_bounded_time_and_the_build_carries_on(monkeypatch):
    built = []

    async def slow_build():
        await asyncio.sleep(0.3)
        built.append(True)
        return object()

    async def never(question):
        raise AssertionError("answered before the ontology was ready")

    monkeypatch.setattr(oq, "get_ontology", slow_build)
    monkeypatch.setattr(oq, "answer_question", never)
    monkeypatch.setattr(oq, "ONTOLOGY_WAIT_S", 0.05)

    async def ask():
        t0 = time.perf_counter()
        out = await ANSWER("Show work-order deviations at Bishopsgate Tower this month")
        waited = time.perf_counter() - t0
        await asyncio.sleep(0.4)
        return out, waited

    out, waited = asyncio.run(ask())
    assert waited < 0.25
    assert out["ok"] is False and out["found"] is False and out["still_loading"] is True
    assert "still loading" in out["answer_hint"]
    assert built == [True], "the build is left to finish for the next question"


def test_once_built_the_question_is_answered(monkeypatch):
    async def ready():
        return object()

    async def answer(question):
        return {"ok": True, "found": True, "records": [], "q": question}

    monkeypatch.setattr(oq, "get_ontology", ready)
    monkeypatch.setattr(oq, "answer_question", answer)
    monkeypatch.setattr(oq, "scope_hint", lambda: "", raising=False)
    out = asyncio.run(ANSWER("open work orders at Bishopsgate"))
    assert out["ok"] is True and out["q"].startswith("open work orders at Bishopsgate")


def test_one_build_is_bounded_and_says_when_it_starts():
    import inspect

    src = inspect.getsource(oq.get_ontology)
    assert "asyncio.wait_for(\n                        build_ontology(s, BASE_SCHEMA), timeout=ONTOLOGY_BUILD_TIMEOUT_S)" in src
    assert '"ontology.build_started"' in src
    # 30 minutes unless the deployment says otherwise (local compose allows 3 hours, once)
    assert 'os.environ.get("ONTOLOGY_BUILD_TIMEOUT_S", "1800")' in inspect.getsource(oq)
