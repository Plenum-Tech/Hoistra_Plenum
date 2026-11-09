"""A migration run that has stopped says so, instead of reading "running" for ever.

Found 6 Nov 2026: run bce6b4d2 had read "running" for 37 days. Its first step finished two seconds
in, on 30 Sep, and nothing ever ran the next one -- the queue of that week accepted every job and ran
none (Redis CROSSSLOT). Nothing on the server knew: the "stalled" judgement lived in the browser,
comparing one poll with the next, so it existed only while somebody had the page open, and it never
changed the run's status. A run that dies with its process -- a container restart kills runs the
API executes inline -- is the same story.

The check: a run whose status is still 'running' but whose row has not been written for
RUN_INTERRUPTED_AFTER_MINUTES (default 30) is marked failed, with an error_message beginning
"Interrupted:" that says when it was last heard from and on which step.

  * failed, not a new status: the screens hard-code which statuses are final in a dozen places,
    and a status one of them does not know would spin for ever -- the bug this exists to end.
    Every one of them already shows a failed run's message, and a failed run can be re-run from
    the step it was on (POST /api/migration/{id}/rerun-from/{node}), from its checkpoint.
  * Silence is read from last_activity_at, which a trigger stamps on EVERY write to the row
    (schema_patches.ensure_activity_is_stamped) -- node logs, the progress beat, gate answers,
    status changes. Without the trigger this does nothing: judged from started_at alone, a busy
    run and a dead one look the same.
  * 30 minutes, from the data: across every run recorded in hoistra_test the slowest step ever
    took 24.6 minutes (Output Generation, which beats its progress as it goes); every other step
    has finished inside 2.4. So 30 minutes of silence is longer than the slowest step even if it
    had written nothing at all. A healthy run writes every few seconds.
  * Only 'running'. A run parked at a gate (awaiting_review) or between steps (step_paused) is
    waiting for a person, which is not a failure however long it takes.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import text

logger = logging.getLogger(__name__)

AFTER_MINUTES = max(5, int(os.environ.get("RUN_INTERRUPTED_AFTER_MINUTES", "30")))
EVERY_SECONDS = max(60, int(os.environ.get("RUN_INTERRUPTED_CHECK_SECONDS", "300")))

_READY = text(
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'plenum_cafm'"
    "                AND table_name = 'migration_jobs' AND column_name = 'last_activity_at')"
    "   AND EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
    "                JOIN pg_namespace n ON n.oid = c.relnamespace"
    "               WHERE n.nspname = 'plenum_cafm' AND c.relname = 'migration_jobs'"
    "                 AND t.tgname = 'migration_jobs_stamp_activity' AND NOT t.tgisinternal)"
)

#: One statement: the condition is re-checked by the UPDATE itself, so a run that wrote anything
#: between a read and a write is never caught. The message is built from the row's own values.
_MARK = text(
    "UPDATE plenum_cafm.migration_jobs"
    "   SET status = 'failed', completed_at = now(), error_timestamp = now(),"
    "       pending_gate_type = NULL, pending_gate_payload = NULL,"
    "       error_message = 'Interrupted: nothing was heard from this run after '"
    "           || to_char(last_activity_at AT TIME ZONE 'UTC', 'DD Mon YYYY HH24:MI') || ' UTC'"
    "           || coalesce(', on step ' || nullif(current_step, ''), '')"
    "           || '. It is no longer running - re-run it from that step to carry on.'"
    " WHERE status = 'running'"
    "   AND last_activity_at IS NOT NULL"
    "   AND last_activity_at < now() - make_interval(mins => :mins)"
    " RETURNING id::text"
)


async def mark_interrupted(engine, *, minutes: int = AFTER_MINUTES) -> list[str]:
    """Mark silent 'running' runs failed. Returns their ids; [] when the stamp is not in place."""
    async with engine.connect() as conn:
        if not (await conn.execute(_READY)).scalar():
            logger.warning("interrupted-run check skipped: the last_activity_at stamp is not in "
                           "place yet, and without it a busy run and a dead one look the same")
            return []
    async with engine.begin() as conn:
        # Row locks on the runs it changes, nothing more; never wait on one -- next round.
        await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
        ids = [r[0] for r in (await conn.execute(_MARK, {"mins": int(minutes)})).all()]
    return ids


async def sweep_once(*, minutes: int = AFTER_MINUTES) -> list[str]:
    """One round: mark, then bring each marked run's Activity Log card up to date with the app's
    own sync (the cancel route does the same), so the feed does not go on showing it running."""
    from ..db import get_async_engine

    ids = await mark_interrupted(get_async_engine(), minutes=minutes)
    if not ids:
        return ids
    logger.warning("interrupted-run check: marked %d run(s) failed after %d silent minutes: %s",
                   len(ids), minutes, ", ".join(i[:8] for i in ids))
    from ..graph.nodes.schema_db_writer import _sync_run_activity

    for rid in ids:
        try:
            await _sync_run_activity(rid, caller="interrupted_check", force=True)
        except Exception as exc:  # noqa: BLE001 - the run is already marked; the card can lag
            logger.warning("interrupted-run check: activity card not updated for %s: %s",
                           rid[:8], str(exc)[:200])
    return ids
