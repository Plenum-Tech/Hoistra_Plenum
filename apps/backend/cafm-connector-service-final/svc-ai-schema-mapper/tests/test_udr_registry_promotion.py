"""Feature 7 — incremental global-admin canonical registry promotion.
Run: python tests/test_udr_registry_promotion.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

import services.registry_promotion as rp  # noqa: E402
from services.registry_promotion import collect_canonical_additions  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── pure: collect additions vs the base plenum_cafm schema ───────────────────
graph = {
    "columns": [
        {"dest_udr_table": "vendors", "column": "vendor_code", "classification": "PK", "cell_format": "id_code"},
        {"dest_udr_table": "vendors", "column": "loyalty_tier", "classification": "SHARED_ATTRIBUTE", "cell_format": "categorical"},
        {"dest_udr_table": "assets", "column": "drone_serial", "classification": "SHARED_ATTRIBUTE"},
        {"dest_udr_table": "", "column": "orphan"},  # no dest table → skipped
    ]
}
base = {"vendors": {"vendor_code", "vendor_name"}, "assets": {"id", "asset_code"}}
adds = collect_canonical_additions(graph, base)
check("existing column not promoted", "vendors.vendor_code" not in adds)
check("new column on existing table promoted", "vendors.loyalty_tier" in adds)
check("new column on second table promoted", "assets.drone_serial" in adds)
check("exactly two additions", len(adds) == 2)

# ── promote / approve merge logic (DB writer stubbed) ────────────────────────
_STORE = {"snap": None, "ver": 0}


async def fake_load_latest(db_url):
    return _STORE["snap"]


async def fake_save(db_url, snapshot, schema_hash=""):
    _STORE["snap"] = snapshot
    _STORE["ver"] += 1
    return _STORE["ver"]


rp.load_latest = fake_load_latest
rp._save_snapshot = fake_save

# Default promotion lands in the pending (admin-approval) area, NOT canonical_fields.
rep = asyncio.run(rp.promote_canonical_additions("db", {"vendors.loyalty_tier": "x", "assets.drone_serial": "y"}))
check("pending by default (none auto-promoted)", rep["pending"] == 2 and rep["promoted"] == 0)
check(
    "pending stored, canonical_fields untouched",
    set(_STORE["snap"]["_pending_canonical"]) == {"vendors.loyalty_tier", "assets.drone_serial"}
    and _STORE["snap"]["canonical_fields"] == {},
)

# Re-promoting the same keys is an idempotent no-op (nothing added, no new version).
rep2 = asyncio.run(rp.promote_canonical_additions("db", {"vendors.loyalty_tier": "x"}))
check("re-promote idempotent (no new version)", rep2["added"] == [] and rep2["version"] is None)

# Admin approval moves pending → canonical_fields (the set the mappers read).
appr = asyncio.run(rp.approve_pending_canonical("db"))
check("approve moves both into canonical", appr["approved"] == 2)
check(
    "canonical_fields now holds both, pending emptied",
    set(_STORE["snap"]["canonical_fields"]) == {"vendors.loyalty_tier", "assets.drone_serial"}
    and _STORE["snap"]["_pending_canonical"] == {},
)

# auto_approve=True promotes straight into canonical_fields (no approval step).
_STORE["snap"] = None
rep3 = asyncio.run(rp.promote_canonical_additions("db", {"sites.green_rating": "z"}, auto_approve=True))
check(
    "auto_approve promotes into canonical_fields",
    rep3["promoted"] == 1 and "sites.green_rating" in _STORE["snap"]["canonical_fields"],
)

# ── 7.11 AC5 backstop: _aged_pending_keys selects pending older than the grace window ──
from datetime import datetime, timedelta, timezone  # noqa: E402

now = datetime(2026, 6, 20, 12, 0, 0, tzinfo=timezone.utc)
old_at = (now - timedelta(hours=30)).isoformat()
fresh_at = (now - timedelta(hours=2)).isoformat()
base = {
    "_pending_canonical": {"vendors.old_col": "x", "vendors.fresh_col": "y"},
    "_promoted": [
        {"keys": ["vendors.old_col"], "auto_approved": False, "at": old_at},
        {"keys": ["vendors.fresh_col"], "auto_approved": False, "at": fresh_at},
    ],
}
aged = rp._aged_pending_keys(base, max_age_hours=24.0, now=now)
check("only the >24h-old pending key is swept", aged == ["vendors.old_col"])

# sweep_pending_canonical approves exactly those aged keys (DB stubbed).
_STORE["snap"] = base
appr2 = asyncio.run(rp.sweep_pending_canonical("db", max_age_hours=24.0, now=now))
check("sweep approves 1 aged pending key", appr2["approved"] == 1)
check("aged key moved to canonical, fresh stays pending",
      "vendors.old_col" in _STORE["snap"]["canonical_fields"]
      and "vendors.fresh_col" in _STORE["snap"]["_pending_canonical"])

print("\nALL TESTS PASSED")
