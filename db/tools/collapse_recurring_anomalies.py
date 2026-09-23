"""Collapse findings that are one fault seen many times into the one finding they are.

The historical sweep stepped a 35-day window forward 7 days at a time, so consecutive windows
shared 28 days of readings and a standing fault was written once per window. The dedup guard
made that certain rather than preventing it: when the run was dated it keyed on
`date(window_end) == as_of`, and no two windows end on the same day.

Each of those rows was independently annualised. Harbour Point held 48 schedule_mismatch rows
carrying the identical 400.0%, marching weekly across the year, summing to GBP576,399 for a
fault whose current rate is about GBP20,000 — and GBP1,393,173 of open anomalies in total
against a GBP972,000 annual energy bill.

anomalies.py now identifies a finding by window overlap, so this will not recur. This applies
the same rule to what is already stored: within a meter and a rule, open findings whose windows
overlap are one chain. The earliest row survives and carries the chain's full span and its most
recent measurement; the rest are absorbed. Findings separated by a real gap stay separate, so a
fault in March and another in July remain two.

The absorbed rows' approvals are withdrawn with them — 160 anomalies raised 160 approvals, and
leaving 150 of those in the queue would move the double-count rather than remove it.

Dry run by default. Pass --apply to write.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict

import asyncpg

#: A finding that has been acted on is history, not a live issue, and is never merged.
SETTLED = ("dismissed", "actioned", "resolved", "closed", "superseded")

#: The sweep's window length. Two findings on the same rule and meter whose windows come
#: within this of each other were looking at some of the same readings.
WINDOW_DAYS = 35


def dsn() -> str:
    raw = os.environ.get("HOISTRA_TEST_DSN")
    if not raw:
        raise SystemExit("HOISTRA_TEST_DSN is not set; source the repo .env first")
    return raw.replace("+asyncpg", "")


async def chains(c) -> dict[tuple, list[list[asyncpg.Record]]]:
    """(meter, rule) -> list of chains, each chain a run of overlapping findings."""
    rows = await c.fetch(f"""
        SELECT a.id, a.meter_id, a.anomaly_type, a.status,
               coalesce(a.window_start, a.detected_at) AS w_start,
               coalesce(a.window_end, a.detected_at)   AS w_end,
               a.detected_at, a.metric_pct::float AS pct, a.excess_kwh::float AS kwh,
               a.annualised_excess_kwh::float AS ann, a.financial_gbp::float AS gbp,
               a.currency, a.tariff_used::float AS tariff, a.detail_json, a.queue_item_id,
               b.name AS building, coalesce(m.mpan, m.mprn) AS supply
          FROM plenum_cafm.energy_anomalies a
          LEFT JOIN plenum_cafm.energy_meters m ON m.id = a.meter_id
          LEFT JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
         WHERE a.status NOT IN ({','.join(f"'{s}'" for s in SETTLED)})
         ORDER BY a.meter_id, a.anomaly_type, w_end
    """)
    grouped: dict[tuple, list[asyncpg.Record]] = defaultdict(list)
    for r in rows:
        grouped[(r["meter_id"], r["anomaly_type"])].append(r)

    out: dict[tuple, list[list[asyncpg.Record]]] = {}
    for key, items in grouped.items():
        runs: list[list[asyncpg.Record]] = []
        for r in items:
            # Overlaps the chain so far? The previous window ended no earlier than this one
            # began. Same test the writer now applies, on the data as it stands.
            if runs and (runs[-1][-1]["w_end"] - r["w_start"]).days >= 0:
                runs[-1].append(r)
            else:
                runs.append([r])
        out[key] = runs
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write; otherwise report only")
    args = ap.parse_args()

    c = await asyncpg.connect(dsn())
    try:
        found = await chains(c)
        absorbed_total = 0
        print(f"\n  {'building':16} {'supply':14} {'rule':24} {'rows':>5} -> {'kept':>4}"
              f"  {'summed GBP':>12}  {'current GBP':>12}")
        print(f"  {'-'*16} {'-'*14} {'-'*24} {'-'*5}    {'-'*4}  {'-'*12}  {'-'*12}")

        for (_meter, rule), runs in sorted(
                found.items(), key=lambda kv: (kv[1][0][0]["building"] or "", kv[0][1])):
            rows = [r for run in runs for r in run]
            was = sum(r["gbp"] or 0 for r in rows)
            now = sum((run[-1]["gbp"] or 0) for run in runs)
            absorbed = len(rows) - len(runs)
            absorbed_total += absorbed
            b, sup = rows[0]["building"] or "-", rows[0]["supply"] or "-"
            print(f"  {b:16} {sup:14} {rule:24} {len(rows):>5} -> {len(runs):>4}"
                  f"  {was:>12,.0f}  {now:>12,.0f}")

            if not args.apply:
                continue

            for run in runs:
                if len(run) == 1:
                    continue
                keep, absorb = run[0], run[1:]
                latest = run[-1]
                # asyncpg hands jsonb back as text, not as a dict.
                raw = keep["detail_json"]
                detail = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
                if not isinstance(detail, dict):
                    detail = {"detail": detail}
                detail["recurrence"] = {
                    "observations": len(run),
                    "first_window_end": keep["w_end"].isoformat(),
                    "latest_window_end": latest["w_end"].isoformat(),
                    "peak_financial_gbp": max((r["gbp"] or 0) for r in run),
                    "peak_metric_pct": max((r["pct"] or 0) for r in run),
                    "collapsed_from": len(run),
                }
                await c.execute("""
                    UPDATE plenum_cafm.energy_anomalies
                       SET window_start = $2, window_end = $3, metric_pct = $4,
                           excess_kwh = $5, annualised_excess_kwh = $6, financial_gbp = $7,
                           tariff_used = $8, detail_json = $9::jsonb, updated_at = now()
                     WHERE id = $1
                """, keep["id"], keep["w_start"], latest["w_end"], latest["pct"],
                     latest["kwh"], latest["ann"], latest["gbp"], latest["tariff"],
                     json.dumps(detail, default=str))

                ids = [r["id"] for r in absorb]
                queue = [r["queue_item_id"] for r in absorb if r["queue_item_id"]]
                if queue:
                    # Withdrawn, not deleted: the queue is a record of what was raised.
                    await c.execute("""
                        UPDATE plenum_cafm.approvals_queue_items
                           SET status = 'dismissed',
                               pm_notes = coalesce(pm_notes || ' | ', '')
                                          || 'withdrawn: duplicate sighting of one standing '
                                          || 'fault, folded into the finding it continues',
                               decided_at = now()
                         WHERE id = ANY($1::uuid[]) AND status NOT IN ('approved','dismissed')
                    """, queue)
                await c.execute(
                    "DELETE FROM plenum_cafm.energy_anomalies WHERE id = ANY($1::uuid[])", ids)

        print(f"\n  {absorbed_total} duplicate sighting(s) "
              f"{'absorbed' if args.apply else 'would be absorbed'}")

        if args.apply:
            print("\n  open anomalies now\n")
            for r in await c.fetch("""
                SELECT b.name AS building, count(*) AS n,
                       round(sum(coalesce(a.financial_gbp,0))) AS gbp,
                       round(max(coalesce(a.financial_gbp,0))) AS largest
                  FROM plenum_cafm.energy_anomalies a
                  JOIN plenum_cafm.buildings b ON b.building_id = a.building_id
                 WHERE a.status = 'open'
                 GROUP BY 1 ORDER BY 1"""):
                print(f"    {r['building']:16} {r['n']:>3} open   GBP {r['gbp']:>9,}  "
                      f"largest GBP {r['largest']:,}")
        else:
            print("  dry run - pass --apply to write")
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(main())
