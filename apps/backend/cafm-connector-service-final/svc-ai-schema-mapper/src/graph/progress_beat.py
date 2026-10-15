"""The heartbeat of a long migration step.

The run card calls a run stalled after three minutes in which the job row says nothing new
(frontend logic/migration.js, progressKey + STALL_AFTER_MS). A step that spends minutes moving
bytes or rows — the output step's Blob uploads, the write step's batches — used to be silent
for all of it, so the card told the user the worker was probably not running while it was
busy (migration f87078d7, 29 Sep 2026: 13 minutes of uploads, 3.5 minutes for one file).

A ProgressBeat maps work done onto a slice of progress_pct and writes it at most every
``every`` seconds. It only moves forward, never writes the same value twice, and never fails
the step it reports on.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


async def _write_pct(migration_id: str, pct: float) -> None:
    from .nodes.db_writer import report_progress_pct

    await report_progress_pct(migration_id, pct)


class ProgressBeat:
    def __init__(
        self,
        migration_id: Optional[str],
        lo: float,
        hi: float,
        *,
        every: float = 15.0,
        write: Callable[[str, float], Awaitable[None]] = _write_pct,
        clock: Callable[[], float] = time.monotonic,
        total: Optional[float] = None,
    ) -> None:
        self.migration_id = migration_id
        #: For advance(): the whole of the work, and how much of it is done.
        self.total, self.done = total, 0.0
        self.lo, self.hi, self.every = float(lo), float(hi), float(every)
        self._write, self._clock = write, clock
        self._last_pct: Optional[float] = None
        self._last_at: Optional[float] = None

    async def advance(self, n: float) -> None:
        """Count ``n`` more units of ``total`` done (a batch of rows, a statement)."""
        self.done += n
        await self.tick(self.done, self.total or 0)

    async def tick(self, done: float, total: float) -> None:
        """Report ``done`` of ``total``; written only when due and only if it moved on."""
        if not self.migration_id or not total or total <= 0:
            return
        now = self._clock()
        if self._last_at is not None and now - self._last_at < self.every:
            return
        frac = min(max(float(done) / float(total), 0.0), 1.0)
        pct = round(self.lo + (self.hi - self.lo) * frac, 1)
        if self._last_pct is not None:
            pct = max(pct, self._last_pct)
            if pct == self._last_pct:
                return
        self._last_pct, self._last_at = pct, now
        try:
            await self._write(self.migration_id, pct)
        except Exception as exc:  # noqa: BLE001 - a heartbeat never fails the step
            logger.warning("[progress_beat] could not write progress for %s: %s", self.migration_id, exc)
