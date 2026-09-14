"""The report clock: one loop, in the service, that refreshes due cards.

The shell used to do this — a 30-second timer that ran while the tab was open, and nothing
when it was not. The loop lives here now, so a card refreshes on its cadence whether or not
anyone is looking. It claims one due card at a time with ``FOR UPDATE SKIP LOCKED``, so
several replicas can run the same loop without asking the same question twice, and a card
whose run died mid-flight (a restart during a refresh) is put back to pending after a grace
period rather than left "running" for ever.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...db import AsyncSessionLocal
from . import cards as card_engine

log = get_logger(__name__)

#: A run still marked running after this long is presumed dead and re-queued.
STALE_RUN_MINUTES = 20

CLAIM_SQL = """
    UPDATE plenum_cafm.report_cards
       SET status = 'running', last_tried_at = now(), updated_at = now()
     WHERE id = (
            SELECT id FROM plenum_cafm.report_cards
             WHERE enabled AND removed_at IS NULL
               AND status <> 'running'
               AND next_run_at IS NOT NULL AND next_run_at <= now()
             ORDER BY next_run_at
             LIMIT 1
             FOR UPDATE SKIP LOCKED)
 RETURNING id
"""

RECOVER_SQL = """
    UPDATE plenum_cafm.report_cards
       SET status = 'pending', updated_at = now(),
           last_error = 'The previous refresh did not finish; it will run again.'
     WHERE status = 'running' AND removed_at IS NULL
       AND last_tried_at < now() - make_interval(mins => :stale)
 RETURNING id
"""


async def claim_due(session: AsyncSession) -> UUID | None:
    """Claim the single most overdue card, or None. The claim is the status change."""
    row = (await session.execute(text(CLAIM_SQL))).scalar_one_or_none()
    await session.commit()
    return UUID(str(row)) if row else None


async def recover_stale(session: AsyncSession, *, stale_minutes: int = STALE_RUN_MINUTES) -> int:
    rows = (await session.execute(text(RECOVER_SQL), {"stale": int(stale_minutes)})).scalars().all()
    await session.commit()
    if rows:
        log.warning("report_scheduler.recovered_stale", count=len(rows))
    return len(rows)


async def tick(*, max_cards: int = 3) -> int:
    """One pass: recover stale runs, then refresh up to ``max_cards`` due cards in turn."""
    ran = 0
    async with AsyncSessionLocal() as session:
        await recover_stale(session)
    for _ in range(max_cards):
        async with AsyncSessionLocal() as session:
            card_id = await claim_due(session)
            if card_id is None:
                break
            await card_engine.run_card(session, card_id, trigger="schedule", claimed=True)
            ran += 1
    return ran


async def run_forever(stop: asyncio.Event) -> None:
    """The loop. Errors are logged and the loop continues — a bad tick is not a dead clock."""
    every = max(5, int(settings.report_scheduler_tick_seconds))
    log.info("report_scheduler.start", tick_seconds=every)
    while not stop.is_set():
        try:
            await tick()
        except Exception as exc:  # noqa: BLE001
            log.warning("report_scheduler.tick_failed", error=str(exc)[:300])
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass
    log.info("report_scheduler.stop")
