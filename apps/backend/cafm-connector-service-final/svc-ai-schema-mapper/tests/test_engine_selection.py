import pytest

from src.engine import selection
from src.engine.selection import ENGINE_GO, ENGINE_PYTHON, choose_engine, uses_go


@pytest.fixture
def engine_present(monkeypatch):
    monkeypatch.setattr(selection, "engine_available", lambda: True)
    monkeypatch.delenv("MIGRATION_ENGINE", raising=False)


@pytest.mark.parametrize("name", ["a.csv", "B.TSV", "x.xlsx", "macro.xlsm"])
def test_spreadsheets_run_on_the_engine(engine_present, name):
    assert choose_engine(source_filename=name) == ENGINE_GO


def test_old_excel_stays_on_python(engine_present):
    assert choose_engine(source_filename="legacy.xls") == ENGINE_PYTHON


def test_the_durable_source_decides_for_a_multi_file_upload(engine_present):
    assert choose_engine(source_filename="a.csv, b.xls",
                         source_blob_path="migrations/m/source/combined.xlsx") == ENGINE_GO


def test_the_kill_switch_wins(engine_present, monkeypatch):
    monkeypatch.setenv("MIGRATION_ENGINE", "python")
    assert choose_engine(source_filename="a.csv") == ENGINE_PYTHON


def test_no_binary_no_engine(monkeypatch):
    monkeypatch.setattr(selection, "engine_available", lambda: False)
    assert choose_engine(source_filename="a.csv") == ENGINE_PYTHON


def test_a_step_uses_go_only_when_the_run_and_the_build_both_say_so(monkeypatch):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"write"}))
    assert uses_go({"engine": "go"}, "write") is True
    assert uses_go({"engine": "go"}, "parse") is False
    assert uses_go({"engine": "python"}, "write") is False
    assert uses_go({}, "write") is False


def test_the_kill_switch_in_the_service_env_file_wins(engine_present, monkeypatch, tmp_path):
    # The all-in-one image carries the deployment's .env into the service directory, where only
    # Settings reads it (pydantic-settings never exports it to os.environ): a MIGRATION_ENGINE line
    # there must switch the engine off as the environment variable does.
    from src import config

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MIGRATION_ENGINE=python\n", encoding="utf-8")
    config.get_settings.cache_clear()
    try:
        assert choose_engine(source_filename="a.csv") == ENGINE_PYTHON
    finally:
        config.get_settings.cache_clear()
