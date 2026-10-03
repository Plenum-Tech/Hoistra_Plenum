"""A Go step's live progress reaches the run card through one Redis key per run.

EngineProgress writes `hoist:engine:progress:<migration id>` (a 120 s TTL, at most every half
second, never failing the step it reports on); UDR's seven stages report through it from the
worker thread they run in; the status route reads the key back with the run's engine.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from src.engine import progress as prog

MID = "2b3c4d5e-6f7a-4b8c-9d0e-1f2a3b4c5d6e"


class FakeRedis:
    def __init__(self, fail: bool = False):
        self.sets: list[tuple[str, str, int]] = []
        self.data: dict[str, str] = {}
        self.fail = fail

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis is down")
        self.sets.append((key, value, ex))
        self.data[key] = value

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis is down")
        return self.data.get(key)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def progress(redis, clock, step="write", **kw):
    return prog.EngineProgress(MID, step, redis=redis, clock=clock, now=lambda: "2026-10-01T15:00:00", **kw)


def ev(done, total=1000, stage="insert", table="meter_readings"):
    return {"type": "progress", "stage": stage, "table": table, "done": done, "total": total}


def test_the_payload_says_where_the_step_is_and_how_fast(tmp_path):
    redis, clock = FakeRedis(), Clock()
    p = progress(redis, clock)
    asyncio.run(p.on_event(ev(100)))
    clock.t += 2
    asyncio.run(p.on_event(ev(300)))
    key, value, ttl = redis.sets[-1]
    assert key == f"hoist:engine:progress:{MID}" and ttl == 120
    assert json.loads(value) == {"engine": "go", "step": "write", "stage": "insert", "table": "meter_readings",
                                 "done": 300, "total": 1000, "rate_per_s": 100, "at": "2026-10-01T15:00:00"}


def test_writes_are_at_most_every_half_second_but_the_last_of_a_table_always_lands():
    redis, clock = FakeRedis(), Clock()
    p = progress(redis, clock)
    asyncio.run(p.on_event(ev(1)))
    clock.t += 0.2
    asyncio.run(p.on_event(ev(2)))
    assert len(redis.sets) == 1
    clock.t += 0.4
    asyncio.run(p.on_event(ev(3)))
    assert len(redis.sets) == 2
    clock.t += 0.1
    asyncio.run(p.on_event(ev(1000)))  # done == total: written whatever the throttle says
    assert len(redis.sets) == 3 and json.loads(redis.sets[-1][1])["done"] == 1000


def test_a_redis_that_is_down_costs_nothing():
    p = progress(FakeRedis(fail=True), Clock())
    asyncio.run(p.on_event(ev(5)))
    asyncio.run(p.stage("unique_tables", 2, 7))
    assert asyncio.run(prog.read_engine_progress(FakeRedis(fail=True), MID)) is None


def test_log_lines_go_to_the_log_and_the_relay_still_sees_every_event():
    seen = []

    async def relay(e):
        seen.append(e)

    redis = FakeRedis()
    p = progress(redis, Clock(), relay=relay)
    asyncio.run(p.on_event({"type": "log", "level": "info", "message": "[Node 9] hello"}))
    asyncio.run(p.on_event(ev(10)))
    assert [e["type"] for e in seen] == ["log", "progress"] and len(redis.sets) == 1


def test_udr_stages_report_as_a_step_of_seven():
    redis, clock = FakeRedis(), Clock()
    p = progress(redis, clock, step="udr")
    asyncio.run(p.stage("prefix_columns", 3, 7))
    body = json.loads(redis.sets[-1][1])
    assert body["step"] == "udr" and body["stage"] == "prefix_columns" and (body["done"], body["total"]) == (3, 7)
    assert body["table"] is None


def test_the_udr_pipeline_calls_on_stage_for_each_stage_in_order():
    from src.udr.pipeline import UDR_STAGES, run_udr_pipeline

    calls = []
    run_udr_pipeline({"T": [{"a": "1"}, {"a": "2"}]}, run_id="r1", on_stage=lambda s, i, n: calls.append((s, i, n)))
    assert UDR_STAGES == ("preprocessing", "unique_tables", "prefix_columns", "vector_chunking", "test1", "test2",
                          "hierarchy")
    assert calls == [(s, i + 1, 7) for i, s in enumerate(UDR_STAGES)]


def test_the_status_fields_read_the_engine_and_its_progress():
    redis = FakeRedis()
    redis.data[f"hoist:engine:progress:{MID}"] = json.dumps({"engine": "go", "step": "parse", "done": 1, "total": 2})
    nodes = [{"node_id": 1, "output": {"engine": "go", "row_count": 5}}, {"node_id": 2, "output": {}}]
    assert asyncio.run(prog.engine_status_fields(nodes, MID, redis)) == (
        "go", {"engine": "go", "step": "parse", "done": 1, "total": 2})
    assert asyncio.run(prog.engine_status_fields([{"node_id": 1, "output": {"row_count": 5}}], MID, FakeRedis())) == (
        None, None)
    # while the parse runs, ingest has written no node log yet: the progress key (only a Go run
    # writes one) says which engine it is
    assert asyncio.run(prog.engine_status_fields([], MID, redis)) == (
        "go", {"engine": "go", "step": "parse", "done": 1, "total": 2})
