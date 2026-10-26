"""trace_lab.orchestrator_host: Trace Lab's own orchestrator (in-memory checkpointer), the isolation
preflight, read-only skill overlays and per-session clean-up (G0 addendum 2.5). No database (the main
engine points at a dead address), no network, no model call: building the orchestrator makes no call."""
from __future__ import annotations

import inspect
import os
import types

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src import database
from src.agents import (activity_log, fiix_agent, meta_tools, migration_agent, schema_mapper_agent,
                        session_workspace, skill_lab, trace)
from src.config import settings
from src.services import chat_memories, chat_threads, skill_overlays
from trace_lab import db, orchestrator_host
from trace_lab.settings import Settings

DEAD = "postgresql+asyncpg://u:p@127.0.0.1:1/plenum"
SID = "golden-1a2b3c4d5e6f-21-r1"


@pytest.fixture
def isolated(monkeypatch):
    """The trace-lab container's environment (addendum 2.2), on a dead database address."""
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    for module in (trace, chat_threads, chat_memories, skill_lab):
        monkeypatch.setattr(module, "_ready", False)
    monkeypatch.setattr(settings, "migration_base_url", "http://127.0.0.1:9")
    for module in (migration_agent, fiix_agent, schema_mapper_agent):   # their _BASE is read at import
        monkeypatch.setattr(module, "_BASE", "http://127.0.0.1:9")
    for attr, key in orchestrator_host.BASE_URL_SETTINGS.items():
        monkeypatch.setattr(settings, attr, f"http://127.0.0.1:8010/__proxy/{key}")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_session_factory", None)
    monkeypatch.setattr(database, "AsyncSessionLocal", None)
    monkeypatch.setattr(skill_overlays, "_ready", skill_overlays._ready)
    monkeypatch.setattr(skill_overlays, "_active", dict(skill_overlays._active))
    monkeypatch.setattr(skill_overlays, "_loaded_at", skill_overlays._loaded_at)
    db.reset_for_tests()
    orchestrator_host.reset_for_tests()
    db.install_main_engine(Settings(main_db_url=DEAD))
    yield
    db.reset_for_tests()
    orchestrator_host.reset_for_tests()


def test_an_isolated_process_has_no_problems(isolated):
    assert orchestrator_host.assert_isolated() == []


@pytest.mark.parametrize("module,name", [(trace, "trace"), (chat_threads, "chat thread"),
                                         (chat_memories, "memory"), (skill_lab, "skill lab")])
def test_an_initialised_store_is_a_problem(isolated, monkeypatch, module, name):
    monkeypatch.setattr(module, "_ready", True)
    (problem,) = orchestrator_host.assert_isolated()
    assert f"The {name} store is initialised" in problem


def test_activity_logging_on_is_a_problem(isolated, monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "true")
    assert activity_log.enabled() is True
    (problem,) = orchestrator_host.assert_isolated()
    assert "ACTIVITY_LOG_ENABLED" in problem


def test_a_live_writer_address_is_a_problem(isolated, monkeypatch):
    monkeypatch.setattr(settings, "migration_base_url", "http://schema-mapper-app:8003")
    (problem,) = orchestrator_host.assert_isolated()
    assert "MIGRATION_BASE_URL" in problem


def test_a_writer_that_kept_a_live_address_from_import_is_a_problem(isolated, monkeypatch):
    monkeypatch.setattr(fiix_agent, "_BASE", "http://schema-mapper-app:8003")
    (problem,) = orchestrator_host.assert_isolated()
    assert "fiix_agent" in problem and "MIGRATION_BASE_URL" in problem


@pytest.mark.parametrize("attr", sorted(orchestrator_host.BASE_URL_SETTINGS))
def test_a_service_address_that_skips_the_proxy_is_a_problem(isolated, monkeypatch, attr):
    monkeypatch.setattr(settings, attr, "http://svc-udr:8006")
    (problem,) = orchestrator_host.assert_isolated()
    assert attr.upper() in problem and "/__proxy/" in problem


def test_a_trailing_slash_on_a_proxy_address_is_fine(isolated, monkeypatch):
    monkeypatch.setattr(settings, "udr_base_url", "http://127.0.0.1:8010/__proxy/udr/")
    assert orchestrator_host.assert_isolated() == []


def test_no_read_only_engine_is_a_problem(isolated, monkeypatch):
    db.reset_for_tests()
    assert orchestrator_host.assert_isolated() == ["The read-only main engine is not installed."]
    db.install_main_engine(Settings(main_db_url=DEAD))
    monkeypatch.setattr(database, "_engine", object())
    assert orchestrator_host.assert_isolated() == [
        "svc-deepagents' database module is not using Trace Lab's read-only engine."]


def test_the_orchestrator_is_built_once_with_an_in_memory_checkpointer(isolated, monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-not-a-real-key")
    first = orchestrator_host.get_orchestrator()
    assert orchestrator_host.get_orchestrator() is first
    assert isinstance(first._agent.checkpointer, MemorySaver) and first._has_hitl is True
    assert database._engine is db.main_engine()            # building it touched no engine
    assert orchestrator_host.assert_isolated() == []


def test_an_orchestrator_without_a_memory_checkpointer_is_a_problem(isolated, monkeypatch):
    monkeypatch.setattr(orchestrator_host, "_orchestrator",
                        types.SimpleNamespace(_agent=types.SimpleNamespace(checkpointer=None)))
    assert orchestrator_host.assert_isolated() == ["The orchestrator was not built with an in-memory checkpointer."]


async def test_overlays_load_read_only_and_a_missing_table_reads_as_none(isolated, monkeypatch):
    monkeypatch.setattr(skill_overlays, "_ready", False)
    monkeypatch.setattr(skill_overlays, "_active", {("x", "y"): "stale"})
    assert await orchestrator_host.ensure_overlays_readonly() == 0      # the dead address: nothing read
    assert skill_overlays._ready is True and skill_overlays._active == {}
    assert orchestrator_host.overlays_loaded() is True
    # refresh() now skips its CREATE TABLE: ensure_table returns at once when _ready is set.
    assert await skill_overlays.ensure_table() is True


def test_release_session_forgets_a_golden_conversation(isolated, monkeypatch, tmp_path):
    saver = MemorySaver()
    saver.storage[SID][""]["c1"] = ("checkpoint",)
    saver.storage["live-session"][""]["c1"] = ("checkpoint",)
    monkeypatch.setattr(orchestrator_host, "_orchestrator",
                        types.SimpleNamespace(_agent=types.SimpleNamespace(checkpointer=saver)))
    monkeypatch.setattr(meta_tools, "_TEMP_DIR", str(tmp_path))
    (tmp_path / SID / "report").mkdir(parents=True)
    (tmp_path / SID / "report" / "a.json").write_text("{}")
    (tmp_path / "live-session").mkdir()
    session_workspace.get_session_state(SID)["conversation_turns"] = [{"role": "user", "content": "q"}]
    meta_tools._MEMORY[SID] = {"k": "v"}
    orchestrator_host.release_session(SID)
    assert SID not in session_workspace._SESSION_STATE and SID not in meta_tools._MEMORY
    assert SID not in saver.storage and "live-session" in saver.storage
    assert not (tmp_path / SID).exists() and (tmp_path / "live-session").exists()


@pytest.mark.parametrize("sid", ["live-session", "golden-../../etc-r1", "", "golden-zz-1-r1"])
def test_release_session_touches_nothing_but_golden_sessions(isolated, monkeypatch, tmp_path, sid):
    monkeypatch.setattr(meta_tools, "_TEMP_DIR", str(tmp_path))
    (tmp_path / "live-session").mkdir()
    session_workspace.get_session_state("live-session")
    orchestrator_host.release_session(sid)
    assert (tmp_path / "live-session").exists() and "live-session" in session_workspace._SESSION_STATE
    session_workspace._SESSION_STATE.pop("live-session", None)


def test_the_private_touch_points_still_exist():
    """Trace Lab sets these svc-deepagents names from outside; a rename there must fail here first."""
    for module in (trace, chat_threads, chat_memories, skill_lab, skill_overlays):
        assert isinstance(getattr(module, "_ready"), bool)
    assert isinstance(skill_overlays._active, dict) and isinstance(skill_overlays._loaded_at, float)
    assert skill_overlays.TABLE == "plenum_cafm.skill_overlays"
    assert "if _ready:" in inspect.getsource(skill_overlays.ensure_table)
    assert "if not await ensure_table():" in inspect.getsource(skill_overlays.refresh)
    assert isinstance(session_workspace._SESSION_STATE, dict) and isinstance(meta_tools._MEMORY, dict)
    assert os.path.basename(meta_tools._TEMP_DIR) == "cafm_deepagent"
    for module in (migration_agent, fiix_agent, schema_mapper_agent):
        assert isinstance(module._BASE, str)                 # read from settings.migration_base_url at import
    from src.agents.orchestrator import DeepAgentOrchestrator
    params = inspect.signature(DeepAgentOrchestrator.__init__).parameters
    assert list(params)[:4] == ["self", "openai_api_key", "model", "checkpointer"]
    assert {"udr_base_url", "operations_intelligence_base_url", "wo_management_base_url", "doc_rag_base_url",
            "migration_base_url", "openai_api_key", "openai_model"} <= set(type(settings).model_fields)
