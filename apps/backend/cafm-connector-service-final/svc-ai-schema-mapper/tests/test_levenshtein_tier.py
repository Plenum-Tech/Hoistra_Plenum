"""Feature 7.4 AC1 — deterministic 'exact OR Levenshtein ≤ 2' tier helpers.
Run: python tests/test_levenshtein_tier.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared-lib"))

from matchers.levenshtein import (  # noqa: E402
    closest_canonical_within as _closest_canonical_within,
    levenshtein as _levenshtein,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── edit distance ──
check("identical = 0", _levenshtein("asset_name", "asset_name") == 0)
check("one substitution = 1", _levenshtein("asset_nime", "asset_name") == 1)
check("one deletion = 1", _levenshtein("assetname", "asset_name") == 1)
check("two edits = 2", _levenshtein("asetnam", "asset_nam") == 2)
check("far apart > 2", _levenshtein("vendor", "asset_name") > 2)

CANON = {"asset_name", "asset_code", "work_order_id", "vendor_name", "site_id", "id"}

# ── within ≤2 → unique closest returned ──
hit = _closest_canonical_within("asset_nme", CANON, max_dist=2)   # 1 edit from asset_name
check("near-typo resolves to asset_name", hit is not None and hit[0] == "asset_name" and hit[1] == 1)

hit2 = _closest_canonical_within("workorderid", CANON, max_dist=2)  # 2 deletions from work_order_id
check("work_order_id within 2", hit2 is not None and hit2[0] == "work_order_id")

# ── no match beyond distance 2 ──
check("unrelated name -> no match", _closest_canonical_within("contractor", CANON, max_dist=2) is None)

# ── short names skipped (too loose) ──
check("short name skipped", _closest_canonical_within("idx", CANON, max_dist=2) is None)

# ── ambiguous tie → None (sent to semantic, not guessed) ──
tie = _closest_canonical_within("asset_nole", {"asset_name", "asset_code"}, max_dist=2)  # 2 from each
check("equidistant tie returns None", tie is None)

print("\nALL TESTS PASSED")
