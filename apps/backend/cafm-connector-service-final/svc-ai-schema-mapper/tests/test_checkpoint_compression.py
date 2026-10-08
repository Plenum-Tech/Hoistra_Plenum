"""Checkpoints go to Postgres compressed.

Every superstep of a run uploads its changed state to the checkpoint tables; on a 2.8 Mbit/s uplink
that was most of the time between gates (2 Oct 2026: 30 MB through Output Generation for one
workbook). The state is msgpack, which zlib shrinks ~7x. Compression is lossless, and a checkpoint
written before it (plain msgpack) still loads, so a run paused today resumes.
"""
from datetime import date, datetime
from uuid import UUID

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from src.graph.checkpoint_serde import CompressedJsonPlus

STATE = {
    "column_intelligence": {f"T{i}": {"cols": [{"name": f"c{j}", "samples": ["B-301-AHU-0%d" % k for k in range(20)]}
                                               for j in range(15)]} for i in range(17)},
    "uuid": UUID("11111111-1111-4111-8111-111111111111"),
    "when": datetime(2026, 10, 2, 9, 0), "day": date(2026, 10, 2),
    "nested": [{"a": None, "b": True, "c": 1.5, "d": "ünïcode ✓"}],
}


def test_a_large_value_is_compressed_and_comes_back_the_same(monkeypatch):
    monkeypatch.setenv("HOIST_CHECKPOINT_COMPRESS", "1")
    s = CompressedJsonPlus()
    t, b = s.dumps_typed(STATE)
    plain_t, plain_b = JsonPlusSerializer().dumps_typed(STATE)
    assert t == plain_t + "+zlib" and len(b) * 4 < len(plain_b)
    assert s.loads_typed((t, b)) == JsonPlusSerializer().loads_typed((plain_t, plain_b))


def test_a_checkpoint_written_before_compression_still_loads():
    s = CompressedJsonPlus()
    for value in (STATE, None, b"raw bytes", bytearray(b"ba"), {"small": 1}):
        assert s.loads_typed(JsonPlusSerializer().dumps_typed(value)) == value


def test_small_values_are_stored_as_before():
    s = CompressedJsonPlus()
    for value in ({"engine": "go"}, None, "x", 3):
        assert s.dumps_typed(value) == JsonPlusSerializer().dumps_typed(value)


def test_bytes_round_trip_compressed(monkeypatch):
    monkeypatch.setenv("HOIST_CHECKPOINT_COMPRESS", "1")
    s = CompressedJsonPlus()
    raw = b"0123456789" * 1000
    t, b = s.dumps_typed(raw)
    assert t == "bytes+zlib" and s.loads_typed((t, b)) == raw


def test_without_the_switch_checkpoints_are_written_as_before(monkeypatch):
    # An older deployment (the revision still running during a deploy, or a rollback) cannot read a
    # compressed value: compression is written only once every reader understands it.
    monkeypatch.delenv("HOIST_CHECKPOINT_COMPRESS", raising=False)
    s = CompressedJsonPlus()
    assert s.dumps_typed(STATE) == JsonPlusSerializer().dumps_typed(STATE)


def test_a_compressed_value_is_read_with_the_switch_off(monkeypatch):
    monkeypatch.setenv("HOIST_CHECKPOINT_COMPRESS", "1")
    stored = CompressedJsonPlus().dumps_typed(STATE)
    monkeypatch.delenv("HOIST_CHECKPOINT_COMPRESS")
    assert CompressedJsonPlus().loads_typed(stored) == JsonPlusSerializer().loads_typed(
        JsonPlusSerializer().dumps_typed(STATE))
