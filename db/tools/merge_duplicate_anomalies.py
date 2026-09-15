"""One event, recorded once — and no approval left for a finding that no longer exists.

Two faults, both from before the scan learned to recognise a live event it had already
raised. They look alike and are not the same thing, so this fixes them separately.

**The register.** On 31 August one weekend spike was written five times and another three
times, minutes apart. Each copy carries whatever a PM did to the copy they happened to open,
so the same event is at once ``expected``, ``monitoring`` and ``open``. Five open anomalies
on this deployment are really two events plus today's two.

**The queue.** Twenty-eight approval items name an anomaly that is not in the register any
more, on meters that are gone too — twenty-four of them still pending. A PM opening the
queue sees thirty-four energy items for what is really four findings. This is the larger
problem and the one that is actually in somebody's way.

What counts as one event is not invented here: ``reports._event_key`` already groups by
(meter, type, metric, amount) when it prices a month, and this uses the same four fields, so
the register and the report cannot disagree about what "one event" means.

The survivor is **the copy carrying the most recent PM decision**, and where none of the
copies was actioned, the earliest detection — the row that first recorded the event. The
decision is then copied onto the survivor if it is missing, so nothing a person recorded is
lost by the merge. Where two copies were actioned differently the newest wins: it is the
call made with the most context.

Nothing of the history goes. ``ops_audit_log`` is append-only and holds every scan and every
PM action; deleting a duplicate row removes a second copy of a finding, not the record of
what anyone did about it.

Dry run by default. ``--apply`` writes, in one transaction.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any

import asyncpg

# A Windows console defaults to cp1252, which has no box-drawing characters: the first
# heading below raised UnicodeEncodeError and took the whole dry run with it.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "plenum_cafm"

#: What makes two rows the same event — the same four fields reports._event_key uses.
EVENT_KEY = ("meter_id", "anomaly_type", "metric_pct", "financial_gbp")

#: The statuses scan_meter_anomalies treats as "this event has been dealt with", copied from
#: _SETTLED_STATUSES there. They matter here because of what they mean for a later detection:
#: once an event is settled, the scan is SUPPOSED to raise it again if the conditions return,
#: and that second row is a new occurrence rather than a duplicate.
#:
#: Without this, a re-detection after a dismissal looks exactly like a duplicate — same
#: meter, same type, same metric, same amount — and deleting it would throw away the fact
#: that the problem came back. hoistra_test has two of those and production has none, which
#: is precisely the pair of cases that would have made this look correct until it was not.
SETTLED_STATUSES = ("resolved", "closed", "dismissed")

#: Queue statuses that still put an item in front of a person.
LIVE_QUEUE = ("pending", "open")


async def groups(c: asyncpg.Connection) -> list[dict[str, Any]]:
    """Every event recorded more than once, with its copies newest-decision-first."""
    rows = await c.fetch(f"""
        SELECT meter_id::text AS meter_id, anomaly_type, metric_pct, financial_gbp,
               count(*) AS copies
          FROM {SCHEMA}.energy_anomalies
         WHERE meter_id IS NOT NULL
         GROUP BY 1, 2, 3, 4
        HAVING count(*) > 1
         ORDER BY count(*) DESC
    """)
    out = []
    for r in rows:
        copies = await c.fetch(f"""
            SELECT id::text, status, pm_action, pm_reason, acted_at, detected_at,
                   queue_item_id::text AS queue_item_id
              FROM {SCHEMA}.energy_anomalies
             WHERE meter_id = $1::uuid AND anomaly_type = $2
               AND metric_pct IS NOT DISTINCT FROM $3
               AND financial_gbp IS NOT DISTINCT FROM $4
             ORDER BY acted_at DESC NULLS LAST, detected_at ASC
        """, r["meter_id"], r["anomaly_type"], r["metric_pct"], r["financial_gbp"])
        # The ordering above IS the rule: the most recent decision first, and where nothing
        # was decided, the earliest detection — the row that first recorded the event.
        rows_ = [dict(x) for x in copies]

        # A row detected after an earlier copy was settled is the event returning, not a
        # second copy of it. Split those out and leave them alone; what remains is one
        # occurrence, however many times it was written.
        settled_at = [x["acted_at"] for x in rows_
                      if x["status"] in SETTLED_STATUSES and x["acted_at"]]
        recurrences = [x for x in rows_ if settled_at and x["detected_at"]
                       and any(x["detected_at"] > t for t in settled_at)]
        occurrence = [x for x in rows_ if x not in recurrences]
        if len(occurrence) < 2:
            continue
        out.append({**dict(r), "copies": len(occurrence), "copies_rows": occurrence,
                    "recurrences": recurrences})
    return out


async def phantom_items(c: asyncpg.Connection) -> list[dict[str, Any]]:
    """Queue items naming an anomaly the register no longer holds."""
    return [dict(r) for r in await c.fetch(f"""
        SELECT q.id::text, q.status, q.created_at, left(q.summary, 90) AS summary
          FROM {SCHEMA}.approvals_queue_items q
         WHERE q.related_entity_type = 'energy_anomaly'
           AND q.status = ANY($1::text[])
           AND NOT EXISTS (SELECT 1 FROM {SCHEMA}.energy_anomalies a
                            WHERE a.id = q.related_entity_id)
         ORDER BY q.created_at
    """, list(LIVE_QUEUE))]


async def run(dsn: str, expect_db: str, apply: bool) -> int:
    c = await asyncpg.connect(dsn, ssl="require", timeout=60)
    db = await c.fetchval("select current_database()")
    if db != expect_db:
        print(f"refusing: connected to {db!r}, expected {expect_db!r}")
        await c.close()
        return 2
    print(f"database: {db}\n")

    found = await groups(c)
    phantoms = await phantom_items(c)

    print("── the register ─────────────────────────────────────────────────────────")
    losers: list[str] = []
    carry: list[tuple[str, dict[str, Any]]] = []
    for g in found:
        keeper, rest = g["copies_rows"][0], g["copies_rows"][1:]
        print(f"  {g['meter_id'][:8]} {g['anomaly_type']} {g['metric_pct']}% — "
              f"{g['copies']} copies of one event")
        print(f"     keep {keeper['id'][:8]} status={keeper['status']:<11} "
              f"action={str(keeper['pm_action']):<14} acted={str(keeper['acted_at'])[:19] or '—'}")
        for r in rest:
            print(f"     drop {r['id'][:8]} status={r['status']:<11} "
                  f"action={str(r['pm_action']):<14} acted={str(r['acted_at'])[:19] or '—'}")
            losers.append(r["id"])
        for r in g.get("recurrences") or []:
            print(f"     keep {r['id'][:8]} status={r['status']:<11} "
                  f"detected {str(r['detected_at'])[:19]} — the event returning after it was "
                  f"settled, not a duplicate")
        # A decision on a dropped copy is not lost: if the survivor never got one, it
        # inherits the most recent one there was.
        if keeper["pm_action"] is None:
            donor = next((r for r in rest if r["pm_action"] is not None), None)
            if donor:
                carry.append((keeper["id"], donor))
                print(f"     carry {donor['pm_action']!r} from {donor['id'][:8]} onto the survivor")
    print(f"\n  {len(found)} events recorded more than once · {len(losers)} rows would go")

    print("\n── the queue ────────────────────────────────────────────────────────────")
    print(f"  {len(phantoms)} live items name an anomaly that is not in the register")
    for p in phantoms[:6]:
        print(f"     {p['id'][:8]} {p['status']:<8} {str(p['created_at'])[:19]}  {p['summary'][:70]}")
    if len(phantoms) > 6:
        print(f"     … and {len(phantoms) - 6} more")
    loser_items = [r["queue_item_id"] for g in found for r in g["copies_rows"][1:]
                   if r["queue_item_id"]]
    print(f"  {len(loser_items)} more belong to the duplicate rows above")

    if not apply:
        print("\ndry run — nothing written. Re-run with --apply.")
        await c.close()
        return 0

    print("\napplying, in one transaction…")
    tr = c.transaction()
    await tr.start()
    try:
        for keeper_id, donor in carry:
            await c.execute(f"""
                UPDATE {SCHEMA}.energy_anomalies
                   SET status = $2, pm_action = $3, pm_reason = $4, acted_at = $5
                 WHERE id = $1::uuid AND pm_action IS NULL
            """, keeper_id, donor["status"], donor["pm_action"], donor["pm_reason"],
                donor["acted_at"])

        # Dismissed rather than deleted: an item a person may already have opened is part of
        # their queue's history, and a reason on it explains why it stopped asking.
        dismissed = 0
        for item_id in [*loser_items, *[p["id"] for p in phantoms]]:
            result = await c.execute(f"""
                UPDATE {SCHEMA}.approvals_queue_items
                   SET status = 'dismissed',
                       pm_notes = coalesce(pm_notes,
                           'dismissed by merge_duplicate_anomalies: a duplicate of an anomaly '
                           'already on the register, or the anomaly it named no longer exists'),
                       decided_at = coalesce(decided_at, now())
                 WHERE id = $1::uuid AND status = ANY($2::text[])
            """, item_id, list(LIVE_QUEUE))
            dismissed += int(result.split()[-1]) if result.split()[-1].isdigit() else 0

        deleted = 0
        if losers:
            result = await c.execute(
                f"DELETE FROM {SCHEMA}.energy_anomalies WHERE id = ANY($1::uuid[])", losers)
            deleted = int(result.split()[-1]) if result.split()[-1].isdigit() else 0

        # Verify before committing: one row per event, and nothing live pointing at nothing.
        still = await groups(c)
        left = await phantom_items(c)
        if still or left:
            await tr.rollback()
            print(f"ROLLED BACK — {len(still)} duplicate groups and {len(left)} phantom items remain")
            await c.close()
            return 1
        await tr.commit()
    except Exception as exc:  # noqa: BLE001
        await tr.rollback()
        print(f"ROLLED BACK — {type(exc).__name__}: {str(exc)[:300]}")
        await c.close()
        return 1

    total = await c.fetchval(f"SELECT count(*) FROM {SCHEMA}.energy_anomalies")
    open_now = await c.fetchval(
        f"SELECT count(*) FROM {SCHEMA}.energy_anomalies WHERE status = 'open'")
    asking = await c.fetchval(
        f"""SELECT count(*) FROM {SCHEMA}.approvals_queue_items
             WHERE related_entity_type = 'energy_anomaly' AND status = ANY($1::text[])""",
        list(LIVE_QUEUE))
    print(f"  {deleted} duplicate rows removed, {len(carry)} decisions carried forward, "
          f"{dismissed} queue items dismissed")
    print(f"\n  register now: {total} rows, {open_now} open")
    print(f"  energy items still asking for a decision: {asking}")
    await c.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--database", default="plenum_agent")
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    dsn = a.dsn
    if not dsn:
        raw = next(l.split("=", 1)[1].strip() for l in
                   open(os.path.join("apps", "backend", ".env"), encoding="utf-8")
                   if l.startswith("DB_URL="))
        dsn = re.sub("^postgresql[+]asyncpg://", "postgresql://", raw)
    dsn = re.sub(r"/[^/?]+(\?|$)", f"/{a.database}\\1", dsn)
    return asyncio.run(run(dsn, a.database, a.apply))


if __name__ == "__main__":
    sys.exit(main())
