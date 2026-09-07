"""Activity log — payload bounding, context propagation, and never-raise behaviour."""
from __future__ import annotations

import asyncio
import json

import pytest

from src.agents import activity_log


def test_bound_payload_passthrough_small():
    out = activity_log.bound_payload({"a": 1, "b": "x"})
    assert out == {"a": 1, "b": "x"}


def test_bound_payload_shortens_long_strings_first():
    big = {"question": "q", "data_json": "x" * 200_000, "rows": list(range(10))}
    out = activity_log.bound_payload(big, limit=10_000)
    assert out["_truncated"] is True
    assert out["_original_chars"] > 200_000
    assert out["question"] == "q"  # structure kept
    assert out["rows"] == list(range(10))
    assert len(json.dumps(out)) <= 10_000
    assert "chars]" in out["data_json"]


def test_bound_payload_falls_back_to_preview_when_still_too_big():
    big = {f"k{i}": "v" * 50 for i in range(2000)}
    out = activity_log.bound_payload(big, limit=3_000)
    assert out["_truncated"] is True and "preview" in out
    assert len(json.dumps(out)) <= 3_200


def test_bound_payload_handles_unserialisable_objects():
    class Thing:
        def __repr__(self):
            return "<Thing>"

    out = activity_log.bound_payload({"obj": Thing(), "s": {1, 2}})
    assert out["obj"] == "<Thing>" and out["s"] == ["1", "2"]


def test_non_dict_payload_is_wrapped():
    assert activity_log.bound_payload("hello") == {"value": "hello"}
    assert activity_log.bound_payload(None) is None


def test_session_context_is_picked_up_by_fire(monkeypatch):
    seen: list[dict] = []

    async def fake_record(**kw):
        seen.append(kw)
        return "id"

    monkeypatch.setattr(activity_log, "record", fake_record)

    async def run():
        activity_log.set_current_session("sess-1", "thread-1")
        activity_log.fire(agent="compliance", stage="plan", direction="input", summary="q")
        await asyncio.sleep(0)
        await asyncio.gather(*list(activity_log._pending))

    asyncio.run(run())
    assert seen and seen[0]["session_id"] == "sess-1" and seen[0]["thread_id"] == "thread-1"
    assert seen[0]["agent"] == "compliance"


def test_record_never_raises_when_db_is_unavailable(monkeypatch):
    # Force a DB failure: the engine getter blows up. record() must log and return None.
    import src.database as db

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(db, "_get_engine", boom)
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "true")
    out = asyncio.run(
        activity_log.record(agent="compliance", stage="plan", direction="output", summary="x",
                            payload={"a": 1}, session_id="s")
    )
    assert out is None


def test_disabled_flag_skips_db(monkeypatch):
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "false")
    assert activity_log.enabled() is False
    out = asyncio.run(activity_log.record(agent="a", stage="b", direction="input"))
    assert out is None
    monkeypatch.setenv("ACTIVITY_LOG_ENABLED", "true")
    assert activity_log.enabled() is True


def test_timed_context_manager_measures():
    with activity_log.timed() as t:
        pass
    assert t.ms >= 0
