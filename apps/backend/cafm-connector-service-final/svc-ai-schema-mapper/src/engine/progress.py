"""A Go step's live progress, for the run card.

The engine reports its progress as events (`{"type": "progress", "stage", "table", "done",
"total"}`); EngineProgress turns them into one small Redis value per run —
`hoist:engine:progress:<migration id>` — which the status route sends with each poll. It lives
120 s, is written at most every half second (the last event of a table always), and is best
effort: Redis being away costs nothing and never fails the step.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

KEY = "hoist:engine:progress:{}"
TTL_S = 120
EVERY_S = 0.5

_redis_client: Any = None


async def _default_redis():
    """The app's Redis client where there is one (the API process), else one of our own (the
    worker); None when Redis is not configured or not reachable."""
    global _redis_client
    try:
        from ..app import get_redis_client

        return get_redis_client()
    except Exception:  # noqa: BLE001 — not the API process, or not initialised
        pass
    if _redis_client is None:
        try:
            from redis import asyncio as aioredis

            from ..config import get_settings

            url = get_settings().redis_url
            if not url:
                return None
            _redis_client = aioredis.from_url(url, decode_responses=True)
        except Exception:  # noqa: BLE001
            return None
    return _redis_client


class EngineProgress:
    """Relay for one Go step's engine events: log lines to the log, progress to Redis, and every
    event to the step's own relay (or its ProgressBeat) as before."""

    def __init__(self, migration_id: Optional[str], step: str, beat=None, *,
                 relay: Optional[Callable[[dict], Awaitable[None]]] = None, redis: Any = None,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], str] = lambda: datetime.utcnow().isoformat()):
        self.migration_id, self.step, self.beat, self.relay = migration_id, step, beat, relay
        self._redis, self._clock, self._now = redis, clock, now
        self._last_write: Optional[float] = None
        self._first: dict[tuple, tuple[float, int]] = {}

    async def on_event(self, ev: dict) -> None:
        if ev.get("type") == "log" and self.relay is None:
            level = {"warning": logging.WARNING, "error": logging.ERROR}.get(str(ev.get("level")), logging.INFO)
            logger.log(level, "%s", ev.get("message"))
        if self.relay is not None:
            await self.relay(ev)
        if ev.get("type") != "progress":
            return
        done, total = int(ev.get("done") or 0), int(ev.get("total") or 0)
        key = (ev.get("stage"), ev.get("table"))
        now = self._clock()
        t0, d0 = self._first.setdefault(key, (now, done))
        rate = int(round((done - d0) / (now - t0))) if now > t0 else 0
        if self.relay is None and self.beat is not None:
            await self.beat.tick(done, total)
        await self._put({"engine": "go", "step": self.step, "stage": ev.get("stage"), "table": ev.get("table"),
                         "done": done, "total": total, "rate_per_s": rate, "at": self._now()},
                        force=total > 0 and done >= total)

    async def stage(self, name: str, index: int, total: int) -> None:
        """A named stage of a Python step reported the same way (the UDR pass's seven)."""
        await self._put({"engine": "go", "step": self.step, "stage": name, "table": None, "done": int(index),
                         "total": int(total), "rate_per_s": 0, "at": self._now()}, force=True)

    async def _put(self, payload: dict, *, force: bool) -> None:
        if not self.migration_id:
            return
        now = self._clock()
        if not force and self._last_write is not None and now - self._last_write < EVERY_S:
            return
        try:
            redis = self._redis if self._redis is not None else await _default_redis()
            if redis is None:
                return
            await redis.set(KEY.format(self.migration_id), json.dumps(payload), ex=TTL_S)
            self._last_write = now
        except Exception as exc:  # noqa: BLE001 — progress is a courtesy, never the step's failure
            logger.debug("[engine] progress write skipped: %s", exc)


async def read_engine_progress(redis: Any, migration_id: str) -> Optional[dict]:
    try:
        if redis is None:
            redis = await _default_redis()
        if redis is None:
            return None
        raw = await redis.get(KEY.format(migration_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


async def engine_status_fields(nodes: Optional[list], migration_id: str, redis: Any = None) -> tuple[Optional[str], Optional[dict]]:
    """(engine, engine_progress) for the status response. Once ingest has logged, its output says
    the engine ("go" on a Go run; a Python run's says nothing, and Redis is not asked). Before that
    — while the parse itself runs — the progress key decides: only a Go run writes one."""
    ingest = next((n for n in nodes or [] if isinstance(n, dict) and n.get("node_id") == 1), None)
    out = ingest.get("output") if ingest else None
    if isinstance(out, dict) and out:
        if out.get("engine") != "go":
            return out.get("engine"), None
        return "go", await read_engine_progress(redis, migration_id)
    progress = await read_engine_progress(redis, migration_id)
    return ("go", progress) if progress else (None, None)
