"""Send past conversations from Hoist Traces to the self-hosted Langfuse (9 Nov 2026).

New turns are sent as they end (src/agents/langfuse_export.py). This sends the ones stored before
that, read from plenum_cafm.agent_trace_turns / agent_trace_spans in a READ ONLY transaction - it
changes nothing in the database. Langfuse treats a trace or observation sent again with the same id as
an update, so running it twice does not duplicate anything.

    LANGFUSE_HOST=... LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... \
        python -m scripts.langfuse_backfill --since 2026-11-01 [--until 2026-11-09] [--org <uuid>] [--limit 500] [--dry-run]

--dry-run prints how many turns and events would be sent and sends nothing. Spans older than
TRACE_RETENTION_DAYS (90) have been purged; such a turn is sent as a trace with no steps.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sqlalchemy import text

from src.agents import langfuse_export as lf
from src.agents.trace import SPANS, TURNS

TURN_COLS = ("turn_id, session_id, organization_id, user_id, email, question, answer, route, started_at, ended_at, "
             "latency_ms, llm_calls, tool_calls, cost_usd, cost_complete, input_tokens, output_tokens, "
             "cache_read_tokens, models, tools, ok, error")
SPAN_COLS = ("id, turn_id, parent_id, seq, kind, name, agent, model, started_at, ended_at, latency_ms, input, output, "
             "input_tokens, output_tokens, cache_read_tokens, cost_usd, ok, error")


def _day(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


async def run(since: datetime, until: datetime | None, org: str | None, limit: int, dry_run: bool) -> int:
    cfg = lf.settings()
    if cfg is None and not dry_run:
        print("Langfuse is not configured: set LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
        return 2
    from src.database import _get_engine

    where = ["started_at >= :since"]
    params: dict = {"since": since, "limit": limit}
    if until:
        where.append("started_at < :until")
        params["until"] = until
    if org:
        where.append("organization_id = CAST(:org AS uuid)")
        params["org"] = org
    async with _get_engine().connect() as conn:
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        turns = (await conn.execute(text(
            f"SELECT {TURN_COLS} FROM {TURNS} WHERE {' AND '.join(where)} ORDER BY started_at LIMIT :limit"),
            params)).mappings().all()
        ids = [r["turn_id"] for r in turns]
        spans_by: dict[str, list[dict]] = {i: [] for i in ids}
        if ids:
            rows = (await conn.execute(text(
                f"SELECT {SPAN_COLS} FROM {SPANS} WHERE turn_id = ANY(:ids) ORDER BY turn_id, seq"),
                {"ids": ids})).mappings().all()
            for r in rows:
                spans_by[r["turn_id"]].append(dict(r))
        await conn.rollback()

    sent = failed = spans_n = 0
    for r in turns:
        spans = spans_by.get(r["turn_id"], [])
        spans_n += len(spans)
        if dry_run:
            continue
        if await lf.send_turn(dict(r), spans, cfg):
            sent += 1
        else:
            failed += 1
    if dry_run:
        print(f"{len(turns)} turns with {spans_n} steps would be sent (nothing sent).")
    else:
        print(f"{sent} of {len(turns)} turns sent ({spans_n} steps); {failed} failed - see the log.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", required=True, help="first day, e.g. 2026-11-01")
    p.add_argument("--until", help="day after the last, e.g. 2026-11-10")
    p.add_argument("--org", help="one company's turns only (organization uuid)")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    return asyncio.run(run(_day(a.since), _day(a.until), a.org, a.limit, a.dry_run))


if __name__ == "__main__":
    sys.exit(main())
