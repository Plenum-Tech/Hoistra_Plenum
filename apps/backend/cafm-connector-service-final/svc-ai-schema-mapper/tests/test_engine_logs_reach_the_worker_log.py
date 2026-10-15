"""The engine's own lines reach the worker log.

The worker never calls configure_logging (only the API's lifespan does), so Python's logging had no
handler there and dropped every INFO line from src.engine — the per-table write timings among them —
while warnings came out bare through logging's last-resort handler (2 Oct 2026).
"""
import io
import logging

from src import worker


def _fresh(monkeypatch):
    root = logging.getLogger()
    eng = logging.getLogger("src.engine")
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(eng, "handlers", [])
    monkeypatch.setattr(eng, "propagate", True)
    monkeypatch.setattr(eng, "level", logging.NOTSET)
    return eng


def test_engine_info_lines_are_printed_in_a_worker(monkeypatch):
    eng = _fresh(monkeypatch)
    worker._engine_logs_to_stdout()
    worker._engine_logs_to_stdout()  # once only, however often the worker starts up
    assert len(eng.handlers) == 1
    out = io.StringIO()
    monkeypatch.setattr(eng.handlers[0], "stream", out)
    logging.getLogger("src.engine.steps").info("[Node 9]   meter_readings: ✓ 277016 inserted (70.9s)")
    line = out.getvalue()
    assert "[Node 9]   meter_readings: ✓ 277016 inserted (70.9s)" in line and "[info" in line


def test_a_process_that_configured_logging_is_left_alone(monkeypatch):
    eng = _fresh(monkeypatch)
    logging.getLogger().addHandler(logging.NullHandler())
    worker._engine_logs_to_stdout()
    assert eng.handlers == []  # the root handler prints them; a second handler would print twice
