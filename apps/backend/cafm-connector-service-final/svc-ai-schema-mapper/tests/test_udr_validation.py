"""Tests for Feature 7 Test 1 / Test 2 engines. Run: python tests/test_udr_validation.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.validation import run_test1_chunk_pk, run_test2_column_fk  # noqa: E402

assets = [
    {"asset_id": "A1", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A2", "make": "Siemens", "site_code": "S1"},
    {"asset_id": "A3", "make": "Trane", "site_code": "S2"},
]
sites = [
    {"site_code": "S1", "city": "Sharjah"},
    {"site_code": "S2", "city": "Ajman"},
]
tables = {
    "assets": {"rows": assets, "columns": ["asset_id", "make", "site_code"], "pk": ["asset_id"]},
    "sites": {"rows": sites, "columns": ["site_code", "city"], "pk": ["site_code"]},
}


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── Test 1: chunk → PK ────────────────────────────────────────────────────────
chunks = [
    {"chunk_id": "c1", "entity_id": "A1"},        # PK of assets → pass
    {"chunk_id": "c2", "entity_id": "S2"},        # PK of sites → pass
    {"chunk_id": "c3", "entity_id": "Siemens"},   # shared attr (make), not a PK → fail
    {"chunk_id": "c4", "entity_id": "ZZZ"},       # nowhere → fail, no found_in
]
r1 = run_test1_chunk_pk(chunks, tables)
check("t1 total 4", r1["total_chunks"] == 4)
check("t1 passed 2", r1["passed"] == 2)
check("t1 failed 2", r1["failed"] == 2)
check("t1 fail_rate 0.5", r1["fail_rate"] == 0.5)
check("t1 blocks (>1%)", r1["blocks_udr"] is True)
fail_c3 = next(f for f in r1["failures"] if f["chunk_id"] == "c3")
check("t1 c3 found_in assets.make", {"table": "assets", "column": "make"} in fail_c3["found_in"])
fail_c4 = next(f for f in r1["failures"] if f["chunk_id"] == "c4")
check("t1 c4 found nowhere", fail_c4["found_in"] == [])

# all-pass case → no block
r1b = run_test1_chunk_pk([{"chunk_id": "x", "entity_id": "A1"}], tables)
check("t1 all pass no block", r1b["blocks_udr"] is False and r1b["pass_rate"] == 1.0)

# ── Test 2: column similarity → FK ────────────────────────────────────────────
# assets.site_code overlaps sites.site_code; declared as FK → explained (pass)
defined_fks = [("assets", "site_code", "sites", "site_code")]
r2 = run_test2_column_fk(tables, defined_fks)
check("t2 has a hit", r2["similarity_hits"] >= 1)
check("t2 site_code explained", r2["flagged_unexplained"] == 0 and r2["explained"] >= 1)
check("t2 no block when explained", r2["blocks_udr"] is False)

# same overlap but NO FK defined → flagged with 3 resolutions, blocks
r2b = run_test2_column_fk(tables, defined_fks=[])
flagged = next(
    (h for h in r2b["flags"] if {h["column_a"], h["column_b"]} == {"site_code"}), None
)
check("t2 unexplained flagged", r2b["flagged_unexplained"] >= 1)
check("t2 flag offers 3 resolutions",
      flagged is not None and flagged["resolutions"] == ["define_fk", "create_reference_table", "mark_coincidental"])
check("t2 blocks when unexplained", r2b["blocks_udr"] is True)
check("t2 overlap reported", flagged["overlap"] == 1.0)

print("\nALL TESTS PASSED")
