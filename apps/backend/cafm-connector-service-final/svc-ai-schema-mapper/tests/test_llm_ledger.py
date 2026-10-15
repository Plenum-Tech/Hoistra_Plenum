"""Each migration run's model spend is recorded against the run, priced, and totalled."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src import llm_ledger as L


def test_cost_is_priced_per_million_tokens_and_a_dated_model_uses_its_family():
    assert L.cost_usd("claude-haiku-4-5-20251001", input_tokens=4000, output_tokens=600) == 0.007
    assert L.cost_usd("claude-sonnet-4-6", input_tokens=20000, output_tokens=4000) == 0.12
    assert L.cost_usd("claude-haiku-4-5-20260101", input_tokens=1_000_000) == 1.0      # family fallback
    assert L.cost_usd("text-embedding-3-small", input_tokens=50_000) == 0.001
    assert L.cost_usd("unknown-model", input_tokens=1000) == 0.0


def test_a_call_is_recorded_only_inside_a_run_with_its_stage_model_and_tokens(monkeypatch):
    rows = []

    async def fake_write(row):
        rows.append(row)

    monkeypatch.setattr(L, "_write", fake_write)

    class _Messages:
        async def create(self, **kw):
            return SimpleNamespace(model=kw["model"], usage=SimpleNamespace(input_tokens=4000, output_tokens=600,
                                                                          cache_read_input_tokens=0, cache_creation_input_tokens=0))

    client = L.wrap_anthropic(SimpleNamespace(messages=_Messages(), api_key="k"))
    assert L.wrap_anthropic(client) is client and client.api_key == "k"      # idempotent; other attributes pass through

    async def deterministic_mapper_call():
        return await client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1000, messages=[])

    async def run():
        await deterministic_mapper_call()                 # outside a run: not recorded
        tok = L.current_migration.set("9f2d6a39-6a46-4b8b-8bf8-1d0e2a6c0b11")
        try:
            await deterministic_mapper_call()
        finally:
            L.current_migration.reset(tok)
        await asyncio.sleep(0)

    asyncio.run(run())
    assert len(rows) == 1
    r = rows[0]
    assert r["migration_id"] == "9f2d6a39-6a46-4b8b-8bf8-1d0e2a6c0b11" and r["model"] == "claude-haiku-4-5-20251001"
    assert (r["input_tokens"], r["output_tokens"], r["cost_usd"], r["ok"]) == (4000, 600, 0.007, True)
    assert r["stage"] == "other"        # this test module is not a pipeline stage


def test_the_stage_is_read_off_the_calling_module():
    ns = {"__name__": "src.graph.nodes.hierarchy_node"}
    exec("def probe():\n    import src.llm_ledger as L\n    return L.stage_of_caller(1)\n", ns)
    assert ns["probe"]() == "hierarchy detection"


def test_a_runs_cost_is_totalled_by_stage_and_by_model():
    class _Res:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _S:
        async def execute(self, stmt, params=None):
            return _Res([
                {"stage": "hierarchy detection", "model": "claude-sonnet-4-6", "calls": 1, "input_tokens": 20000, "output_tokens": 4000,
                 "cache_read_tokens": 0, "cost_usd": 0.12, "latency_ms": 30000, "failed": 0},
                {"stage": "field mapping (Tier 1)", "model": "claude-haiku-4-5-20251001", "calls": 8, "input_tokens": 32000, "output_tokens": 4800,
                 "cache_read_tokens": 0, "cost_usd": 0.056, "latency_ms": 16000, "failed": 1},
                {"stage": "per-field fallback", "model": "claude-haiku-4-5-20251001", "calls": 30, "input_tokens": 45000, "output_tokens": 1500,
                 "cache_read_tokens": 0, "cost_usd": 0.0525, "latency_ms": 9000, "failed": 0}])

    out = asyncio.run(L.run_cost(_S(), "m1"))
    assert out["recorded"] and out["total"]["calls"] == 39 and out["total"]["cost_usd"] == 0.2285 and out["total"]["failed"] == 1
    assert [s["stage"] for s in out["by_stage"]] == ["hierarchy detection", "field mapping (Tier 1)", "per-field fallback"]
    assert out["by_model"][0]["model"] == "claude-sonnet-4-6" and out["by_model"][1]["calls"] == 38
