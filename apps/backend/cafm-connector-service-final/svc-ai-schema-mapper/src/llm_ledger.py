"""What each migration run spends on models - every call recorded against the run that made it.

Until 5 Oct 2026 a migration's model cost was recorded nowhere (claude_api_usage had no rows), so
pricing a migration for a customer meant estimating from the code. Now:

    get_anthropic_client() / get_openai_client()   return the real client wrapped (wrap_*): each
                                                    messages.create / embeddings.create is timed and
                                                    its usage recorded
    MigrationGraphProxy.ainvoke                     sets `current_migration` from the run's thread id,
                                                    so every node's calls carry the run
    plenum_cafm.migration_llm_usage                 one row per call: run, stage, model, tokens, $, ms
    GET /api/migration/{id}/cost                    totals by stage and by model

A call outside a run (the canonical embeddings warm-up at start) is not recorded. Recording never
fails a call: a write that does not go is logged and the answer is returned as it came.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

log = structlog.get_logger(__name__)

TABLE = "plenum_cafm.migration_llm_usage"

#: Dollars per million tokens. LLM_PRICES_JSON overrides or adds (same shape), as in svc-deepagents.
PRICES: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0},
}

#: The run the current task belongs to (set by MigrationGraphProxy.ainvoke).
current_migration: ContextVar[str | None] = ContextVar("migration_ledger_run", default=None)

#: Module of the calling code -> the pipeline stage a reader recognises.
STAGES = {
    "deterministic_mapper": "field mapping (Tier 1)",
    "schema_deterministic_node": "per-field fallback",
    "dataset_describer": "dataset description",
    "hierarchy_node": "hierarchy detection",
    "llm_discovery": "UDR column intelligence",
    "mapping_doc_parser": "mapping document",
    "embeddings": "semantic match (Tier 2)",
}

_DDL = [
    f"""CREATE TABLE IF NOT EXISTS {TABLE} (
        id            UUID PRIMARY KEY,
        migration_id  UUID NOT NULL,
        stage         TEXT,
        model         TEXT,
        calls         INTEGER NOT NULL DEFAULT 1,
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_write_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd      NUMERIC(12,6) NOT NULL DEFAULT 0,
        latency_ms    INTEGER,
        ok            BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_migration_llm_usage_run ON {TABLE} (migration_id, created_at)",
]

_ready = False


def prices() -> dict[str, dict[str, float]]:
    raw = os.getenv("LLM_PRICES_JSON", "").strip()
    if not raw:
        return PRICES
    try:
        return {**PRICES, **json.loads(raw)}
    except ValueError:
        return PRICES


def price_of(model: str | None) -> dict[str, float] | None:
    table = prices()
    m = str(model or "")
    if m in table:
        return table[m]
    # a dated or suffixed id ("claude-haiku-4-5-20251001") falls back to its family
    for k in sorted(table, key=len, reverse=True):
        if m.startswith(k):
            return table[k]
    return None


def cost_usd(model: str | None, *, input_tokens: int = 0, output_tokens: int = 0,
             cache_read_tokens: int = 0, cache_write_tokens: int = 0) -> float:
    p = price_of(model)
    if not p:
        return 0.0
    return round((input_tokens * p["input"] + output_tokens * p["output"] + cache_read_tokens * p["cache_read"]
                  + cache_write_tokens * p["cache_write"]) / 1_000_000, 6)


def stage_of_caller(depth: int = 3) -> str:
    """The stage name for the module that made the call, read off the stack."""
    try:
        f = sys._getframe(depth)  # noqa: SLF001 - a lookup, not control flow
        for _ in range(8):
            if f is None:
                break
            mod = str(f.f_globals.get("__name__") or "").rsplit(".", 1)[-1]
            if mod in STAGES:
                return STAGES[mod]
            f = f.f_back
    except Exception:  # noqa: BLE001
        pass
    return "other"


def _usage_anthropic(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    g = (lambda k: int(getattr(u, k, 0) or 0)) if u is not None else (lambda k: 0)
    return {"input_tokens": g("input_tokens"), "output_tokens": g("output_tokens"),
            "cache_read_tokens": g("cache_read_input_tokens"), "cache_write_tokens": g("cache_creation_input_tokens")}


def _usage_openai(resp: Any) -> dict[str, int]:
    u = getattr(resp, "usage", None)
    return {"input_tokens": int(getattr(u, "prompt_tokens", 0) or getattr(u, "total_tokens", 0) or 0) if u is not None else 0,
            "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


def _record(model: str | None, usage: dict[str, int], ms: float, ok: bool, stage: str) -> None:
    run = current_migration.get()
    if not run:
        return
    row = {"id": str(uuid.uuid4()), "migration_id": run, "stage": stage, "model": str(model or "unknown"),
           "cost_usd": cost_usd(model, **usage), "latency_ms": int(ms), "ok": ok, **usage}
    try:
        asyncio.get_running_loop().create_task(_write(row))
    except RuntimeError:
        pass


async def _write(row: dict[str, Any]) -> None:
    global _ready
    try:
        from sqlalchemy import text

        from .db import get_async_engine

        async with get_async_engine().begin() as conn:
            if not _ready:
                for stmt in _DDL:
                    await conn.execute(text(stmt))
                _ready = True
            await conn.execute(text(f"""
                INSERT INTO {TABLE} (id, migration_id, stage, model, input_tokens, output_tokens, cache_read_tokens,
                                     cache_write_tokens, cost_usd, latency_ms, ok)
                VALUES (CAST(:id AS uuid), CAST(:migration_id AS uuid), :stage, :model, :input_tokens, :output_tokens,
                        :cache_read_tokens, :cache_write_tokens, :cost_usd, :latency_ms, :ok)"""), row)
    except Exception as exc:  # noqa: BLE001 - recording never fails the call it records
        log.warning("migration_ledger.write_failed", error=str(exc)[:200])


class _Recorded:
    """`target.<method>(...)` awaited and recorded; everything else passes through."""

    def __init__(self, target: Any, method: str, usage_of) -> None:
        self._target, self._method, self._usage_of = target, method, usage_of

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if name != self._method:
            return attr

        async def call(*args: Any, **kwargs: Any) -> Any:
            stage = stage_of_caller(2)
            t0 = time.perf_counter()
            try:
                resp = await attr(*args, **kwargs)
            except Exception:
                _record(kwargs.get("model"), {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
                        (time.perf_counter() - t0) * 1000, False, stage)
                raise
            _record(getattr(resp, "model", None) or kwargs.get("model"), self._usage_of(resp),
                    (time.perf_counter() - t0) * 1000, True, stage)
            return resp

        return call


class _Client:
    def __init__(self, client: Any, attr: str, method: str, usage_of) -> None:
        self._client, self._attr = client, attr
        self._wrapped = _Recorded(getattr(client, attr), method, usage_of)

    def __getattr__(self, name: str) -> Any:
        return self._wrapped if name == self._attr else getattr(self._client, name)


def wrap_anthropic(client: Any) -> Any:
    return client if client is None or isinstance(client, _Client) else _Client(client, "messages", "create", _usage_anthropic)


def wrap_openai(client: Any) -> Any:
    return client if client is None or isinstance(client, _Client) else _Client(client, "embeddings", "create", _usage_openai)


async def run_cost(session, migration_id: str) -> dict[str, Any]:
    """Totals for one run: overall, by stage and by model."""
    from sqlalchemy import text

    try:
        rows = (await session.execute(text(f"""
            SELECT stage, model, count(*) AS calls, sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                   sum(cache_read_tokens) AS cache_read_tokens, sum(cost_usd) AS cost_usd, sum(latency_ms) AS latency_ms,
                   count(*) FILTER (WHERE NOT ok) AS failed
              FROM {TABLE} WHERE migration_id = CAST(:m AS uuid) GROUP BY stage, model ORDER BY sum(cost_usd) DESC"""),
            {"m": migration_id})).mappings().all()
    except Exception as exc:  # noqa: BLE001 - no table yet: nothing recorded
        log.info("migration_ledger.read_empty", error=str(exc)[:120])
        rows = []
    lines = [{k: (float(v) if k == "cost_usd" else int(v or 0) if k not in ("stage", "model") else v) for k, v in dict(r).items()} for r in rows]

    def roll(key: str) -> list[dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for x in lines:
            o = out.setdefault(x[key], {key: x[key], "calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
            for k in ("calls", "input_tokens", "output_tokens"):
                o[k] += x[k]
            o["cost_usd"] = round(o["cost_usd"] + x["cost_usd"], 6)
        return sorted(out.values(), key=lambda o: -o["cost_usd"])

    total = {"calls": sum(x["calls"] for x in lines), "input_tokens": sum(x["input_tokens"] for x in lines),
             "output_tokens": sum(x["output_tokens"] for x in lines), "cache_read_tokens": sum(x["cache_read_tokens"] for x in lines),
             "cost_usd": round(sum(x["cost_usd"] for x in lines), 6), "failed": sum(x["failed"] for x in lines)}
    return {"ok": True, "migration_id": migration_id, "recorded": bool(lines), "total": total,
            "by_stage": roll("stage"), "by_model": roll("model"), "currency": "USD"}
