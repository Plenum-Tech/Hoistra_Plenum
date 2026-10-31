"""trace_lab.settings: environment only, pure, a ValueError that names the variable (G0 addendum 2.2)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from trace_lab import settings

MAIN = "postgresql+asyncpg://u:p@db.example:5432/plenum?sslmode=require"
LAB = "postgresql+asyncpg://u:p@db.example:5432/hoist_tracelab?sslmode=require"


def test_defaults_from_an_empty_environment():
    s = settings.load({})
    assert s == settings.Settings()
    assert (s.main_db_url, s.lab_db_url, s.schema_mode, s.enabled) == ("", "", "verify", False)
    assert s.upstreams == {"udr": "", "oi": "", "wom": "", "docrag": ""}
    assert s.judge_model is None and s.golden_max_concurrency == 1


def test_a_full_environment():
    s = settings.load({
        "DB_URL": MAIN, "TRACE_LAB_DB_URL": LAB, "TRACE_LAB_SCHEMA": " APPLY ", "TRACE_LAB_ENABLED": "1",
        "TRACE_LAB_UPSTREAM_UDR": "http://svc-udr:8006/", "TRACE_LAB_UPSTREAM_OI": "http://svc-operations-intelligence:8009",
        "TRACE_LAB_UPSTREAM_WOM": "http://backend-app:8007", "TRACE_LAB_UPSTREAM_DOCRAG": "https://docs.example",
        "TRACE_LAB_JUDGE_MODEL": "claude-sonnet-5", "TRACE_LAB_GOLDEN_MAX_CONCURRENCY": "2",
    })
    assert (s.main_db_url, s.lab_db_url, s.schema_mode, s.enabled) == (MAIN, LAB, "apply", True)
    assert s.upstreams == {"udr": "http://svc-udr:8006", "oi": "http://svc-operations-intelligence:8009",
                           "wom": "http://backend-app:8007", "docrag": "https://docs.example"}
    assert s.judge_model == "claude-sonnet-5" and s.golden_max_concurrency == 2


def test_database_url_is_read_like_svc_deepagents_reads_it():
    assert settings.load({"DATABASE_URL": MAIN}).main_db_url == MAIN
    assert settings.load({"DB_URL": MAIN, "DATABASE_URL": "postgresql+asyncpg://x@y/z"}).main_db_url == MAIN


@pytest.mark.parametrize("env, variable", [
    ({"DB_URL": "postgresql://u:p@h/db"}, "DB_URL"),
    ({"DATABASE_URL": "mysql://u:p@h/db"}, "DATABASE_URL"),
    ({"TRACE_LAB_DB_URL": "postgres://u:p@h/db"}, "TRACE_LAB_DB_URL"),
    ({"TRACE_LAB_SCHEMA": "migrate"}, "TRACE_LAB_SCHEMA"),
    ({"TRACE_LAB_ENABLED": "maybe"}, "TRACE_LAB_ENABLED"),
    ({"TRACE_LAB_UPSTREAM_UDR": "svc-udr:8006"}, "TRACE_LAB_UPSTREAM_UDR"),
    ({"TRACE_LAB_UPSTREAM_DOCRAG": "ftp://docs"}, "TRACE_LAB_UPSTREAM_DOCRAG"),
    ({"TRACE_LAB_GOLDEN_MAX_CONCURRENCY": "two"}, "TRACE_LAB_GOLDEN_MAX_CONCURRENCY"),
    ({"TRACE_LAB_GOLDEN_MAX_CONCURRENCY": "0"}, "TRACE_LAB_GOLDEN_MAX_CONCURRENCY"),
    ({"TRACE_LAB_GOLDEN_MAX_CONCURRENCY": "5"}, "TRACE_LAB_GOLDEN_MAX_CONCURRENCY"),
])
def test_a_bad_value_names_its_variable(env, variable):
    with pytest.raises(ValueError, match=variable):
        settings.load(env)


@pytest.mark.parametrize("raw, on", [("1", True), ("true", True), ("ON", True), ("0", False), ("", False), ("no", False)])
def test_the_kill_switch(raw, on):
    assert settings.load({"TRACE_LAB_ENABLED": raw}).enabled is on


def test_load_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("TRACE_LAB_SCHEMA", "apply")
    monkeypatch.setenv("TRACE_LAB_ENABLED", "1")
    s = settings.load()
    assert s.schema_mode == "apply" and s.enabled is True


def test_settings_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.Settings().schema_mode = "apply"  # type: ignore[misc]


def test_settings_import_no_application_code():
    text = Path(settings.__file__).read_text(encoding="utf-8")
    assert "from src" not in text and "import src" not in text
