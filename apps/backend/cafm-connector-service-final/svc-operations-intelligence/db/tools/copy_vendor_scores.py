"""Copy vendor performance rows (work-order scores, monthly scorecards) from one database
into another, re-keyed onto the target's own vendors, work orders and assets.

hoistra_test had no vendor_wo_scores and no vendor_monthly_scorecards, so the Vendors page
there had nothing to show and the vendor-scoped queries had nothing to narrow. plenum_agent
has 2,801 scores and 29 scorecards over four vendors. The data is synthetic (owner's
confirmation), so it is carried over and placed on the target's records by a STABLE rule:

  vendor      — a source vendor maps to the target vendor sharing a trade word in its name
                (Electrical → Northgate Electrical, Lift → Apex Lift Services, Mechanical →
                Gulf Mechanical); a vendor with no such match takes one of the remaining
                target vendors by md5(source id) — the same one every run.
  work order  — each score lands on one of the target work orders of its mapped vendor, by
                md5(score id) mod n; its asset is that work order's asset. wo_code keeps the
                source code for provenance.
  ids         — uuid5(namespace, source id), so a re-run inserts nothing twice.
  organisation— the target's own organisation row.

    python db/tools/copy_vendor_scores.py --from plenum_agent --to hoistra_test          # dry run
    python db/tools/copy_vendor_scores.py --from plenum_agent --to hoistra_test --apply  # one transaction
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from typing import Any

import asyncpg

sys.stdout.reconfigure(encoding="utf-8")

ROOT_ENV = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "..", ".env")
#: Fixed namespace so a source id always maps to the same target id — re-runs insert nothing twice.
NAMESPACE = uuid.UUID("7d1f6c2e-3b0a-5e4d-9c8f-a1b2c3d4e5f6")
TRADE_WORDS = ("electric", "lift", "mechanical", "fire", "security", "hygiene", "clean", "hvac", "plumb")


def dsn() -> str:
    raw = os.environ.get("PLENUM_DB_DSN")
    if not raw and os.path.exists(ROOT_ENV):
        for line in open(ROOT_ENV, encoding="utf-8"):
            if line.startswith("PLENUM_DB_DSN="):
                raw = line.split("=", 1)[1].strip().strip('"')
    if not raw:
        raise SystemExit("PLENUM_DB_DSN not set")
    return re.sub(r"^postgresql\+asyncpg://", "postgresql://", raw)


def spread(key: str, n: int) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16) % max(1, n)


def new_id(source_id: Any) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, str(source_id))


def map_vendors(source: list[dict], target: list[dict]) -> dict[str, dict]:
    """source vendor id → target vendor row. Trade word first, then a stable spread over the
    target vendors not yet taken (or all of them, once every one is taken)."""
    out: dict[str, dict] = {}
    taken: set[str] = set()
    for s in source:
        name = (s["vendor_name"] or "").lower()
        hit = None
        for word in TRADE_WORDS:
            if word in name:
                hit = next((t for t in target if word in (t["vendor_name"] or "").lower()), None)
                if hit:
                    break
        if hit:
            out[s["id"]] = {**hit, "rule": f"trade word '{word}'"}
            taken.add(hit["id"])
    for s in source:
        if s["id"] in out:
            continue
        pool = [t for t in target if t["id"] not in taken] or target
        pick = pool[spread(s["id"], len(pool))]
        out[s["id"]] = {**pick, "rule": "stable spread"}
        taken.add(pick["id"])
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--to", dest="dst", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    d = dsn()
    src = await asyncpg.connect(d, database=args.src, timeout=30)
    dst = await asyncpg.connect(d, database=args.dst, timeout=30)
    print(f"== {args.src} → {args.dst} — {'APPLY' if args.apply else 'dry run'}")

    scores = [dict(r) for r in await src.fetch("SELECT * FROM plenum_cafm.vendor_wo_scores ORDER BY score_month, id")]
    cards = [dict(r) for r in await src.fetch("SELECT * FROM plenum_cafm.vendor_monthly_scorecards ORDER BY score_month, id")]
    src_vendors = [dict(r) for r in await src.fetch("""
        SELECT v.id::text AS id, v.vendor_name FROM plenum_cafm.vendors v
         WHERE v.id::text IN (SELECT vendor_id::text FROM plenum_cafm.vendor_wo_scores
                              UNION SELECT vendor_id::text FROM plenum_cafm.vendor_monthly_scorecards)
         ORDER BY v.vendor_name""")]
    tgt_vendors = [dict(r) for r in await dst.fetch(
        "SELECT id::text AS id, vendor_name FROM plenum_cafm.vendors WHERE id::text ~ '^[0-9a-f]{8}-' ORDER BY vendor_name")]
    tgt_org = await dst.fetchval("SELECT id FROM plenum_cafm.organizations ORDER BY (id::text = '00000000-0000-0000-0000-000000000001') DESC, created_at LIMIT 1")
    wos = [dict(r) for r in await dst.fetch(
        "SELECT id::text AS id, vendor_id::text AS vendor_id, asset_id::text AS asset_id FROM plenum_cafm.work_orders WHERE vendor_id IS NOT NULL ORDER BY id")]
    wos_by_vendor: dict[str, list[dict]] = {}
    for w in wos:
        wos_by_vendor.setdefault(w["vendor_id"], []).append(w)
    if not tgt_vendors or not wos:
        raise SystemExit("target has no uuid-keyed vendors or no work orders with a vendor — nothing to place on")

    vmap = map_vendors(src_vendors, tgt_vendors)
    print("  vendor map:")
    for s in src_vendors:
        t = vmap[s["id"]]
        n = sum(1 for x in scores if str(x["vendor_id"]) == s["id"])
        print(f"    {s['vendor_name']:<32} → {t['vendor_name']:<28} ({t['rule']}) · {n} scores · {len(wos_by_vendor.get(t['id'], []))} target work orders")

    def target_wo(score_id: Any, vendor_id: str) -> dict:
        pool = wos_by_vendor.get(vendor_id) or wos
        return pool[spread(str(score_id), len(pool))]

    existing_scores = {str(r) for r in await dst.fetchval("SELECT coalesce(array_agg(id), '{}') FROM plenum_cafm.vendor_wo_scores")}
    existing_cards = {str(r) for r in await dst.fetchval("SELECT coalesce(array_agg(id), '{}') FROM plenum_cafm.vendor_monthly_scorecards")}

    score_rows, skipped_v = [], 0
    for s in scores:
        sv = str(s["vendor_id"]) if s["vendor_id"] else None
        if sv not in vmap:
            skipped_v += 1
            continue
        t = vmap[sv]
        w = target_wo(s["id"], t["id"])
        nid = new_id(s["id"])
        if str(nid) in existing_scores:
            continue
        score_rows.append((nid, tgt_org, uuid.UUID(t["id"]), uuid.UUID(w["id"]), s["wo_code"],
                           uuid.UUID(w["asset_id"]) if w["asset_id"] else None, s["score_month"],
                           s["sla_response_met"], s["sla_completion_met"], s["first_fix"], s["recall"],
                           s["accreditation_current"], s["component_scores"], s["overall_score"], s["capped_by_block"],
                           s["cost_actual"], s["cost_estimated"], s["cost_variance_pct"], s["created_at"]))
    card_rows = []
    for c in cards:
        sv = str(c["vendor_id"]) if c["vendor_id"] else None
        if sv not in vmap:
            continue
        nid = new_id(c["id"])
        if str(nid) in existing_cards:
            continue
        ids = c["wo_score_ids"]
        ids = json.loads(ids) if isinstance(ids, str) else (ids or [])
        remapped = json.dumps([str(new_id(i)) for i in ids])
        breakdown = c["component_breakdown"]
        breakdown = json.loads(breakdown) if isinstance(breakdown, str) else (breakdown or {})
        # A contract id from the source database means nothing here; the figure stays, the pointer goes.
        breakdown.pop("contract_parameters_id", None)
        card_rows.append((nid, tgt_org, uuid.UUID(vmap[sv]["id"]), c["score_month"], c["overall_score"], c["trend_delta"],
                          json.dumps(breakdown), c["ppm_compliance_pct"], c["matched_flagged_ratio"], remapped,
                          c["block_capped"], c["created_at"]))

    print(f"  scores: {len(scores)} source → {len(score_rows)} to insert ({skipped_v} with no mappable vendor, "
          f"{len(scores) - len(score_rows) - skipped_v} already present)")
    print(f"  scorecards: {len(cards)} source → {len(card_rows)} to insert")
    if not args.apply:
        print("  dry run — nothing written; re-run with --apply")
        await src.close(); await dst.close()
        return

    async with dst.transaction():
        await dst.executemany("""
            INSERT INTO plenum_cafm.vendor_wo_scores
                (id, organization_id, vendor_id, work_order_id, wo_code, asset_id, score_month, sla_response_met,
                 sla_completion_met, first_fix, recall, accreditation_current, component_scores, overall_score,
                 capped_by_block, cost_actual, cost_estimated, cost_variance_pct, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15,$16,$17,$18,$19)
            ON CONFLICT DO NOTHING""", [tuple(json.dumps(v) if isinstance(v, (dict, list)) else v for v in r) for r in score_rows])
        await dst.executemany("""
            INSERT INTO plenum_cafm.vendor_monthly_scorecards
                (id, organization_id, vendor_id, score_month, overall_score, trend_delta, component_breakdown,
                 ppm_compliance_pct, matched_flagged_ratio, wo_score_ids, block_capped, created_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9,$10::jsonb,$11,$12)
            ON CONFLICT DO NOTHING""", card_rows)
    n_s = await dst.fetchval("SELECT count(*) FROM plenum_cafm.vendor_wo_scores")
    n_c = await dst.fetchval("SELECT count(*) FROM plenum_cafm.vendor_monthly_scorecards")
    print(f"  committed — target now holds {n_s} scores and {n_c} scorecards")
    await src.close(); await dst.close()


if __name__ == "__main__":
    asyncio.run(main())
