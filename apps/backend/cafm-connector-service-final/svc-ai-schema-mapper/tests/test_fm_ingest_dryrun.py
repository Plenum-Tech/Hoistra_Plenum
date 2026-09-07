"""Layer-1 ingest DRY RUN over the two FM source files (the same logic the UI pipeline runs).

Loads tests/fixtures/fm_source_primary.xlsx (5 sheets) + work_tasks.csv as ONE batch, then runs
the real pure backend modules — pre-processing, unique-table identification, the UDR pipeline,
and FM-ontology Step-1b resolution — and prints a report + asserts the expected outcomes. Use it
to cross-check what the UI shows after uploading the same two files.

Run:  python tests/test_fm_ingest_dryrun.py
"""
import importlib
import os
import sys
import types

import pandas as pd

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)

# udr modules import cleanly (no cafm_shared)
from udr.preprocessing import (  # noqa: E402
    find_column_merge_candidates, find_same_name_divergent_columns, nan_stats,
    preprocessing_summary, propose_column_name, remove_exact_duplicate_rows,
)
from udr.primitives import build_table_metadata, classify_columns, detect_primary_key  # noqa: E402
from udr.pipeline import run_udr_pipeline  # noqa: E402
from udr.unique_tables import identify_unique_tables  # noqa: E402

# fm_ontology via a stub 'matchers' package (skip the heavy __init__ that needs cafm_shared)
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg
FM = importlib.import_module("matchers.fm_ontology")

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def _records(df: pd.DataFrame) -> list[dict]:
    """NaN -> None, like ingest_node._sanitize_records."""
    return df.astype(object).where(pd.notna(df), None).to_dict(orient="records")


def _columns(rows: list[dict]) -> list[str]:
    cols, seen = [], set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k); cols.append(k)
    return cols


def _drop_all_null_columns(rows, columns):
    keep = [c for c in columns if any(r.get(c) not in (None, "") for r in rows)]
    dropped = [c for c in columns if c not in keep]
    return [{c: r.get(c) for c in keep} for r in rows], keep, dropped


# ── load the two files as ONE batch ──────────────────────────────────────────
sheets = pd.read_excel(os.path.join(FIX, "fm_source_primary.xlsx"), sheet_name=None)
raw = {name: _records(df) for name, df in sheets.items()}
raw["work_tasks"] = _records(pd.read_csv(os.path.join(FIX, "work_tasks.csv")))

print("=" * 70)
print("BATCH: %d source tables ->" % len(raw), ", ".join(raw))
print("=" * 70)

# ── Stage 1: pre-processing per table ────────────────────────────────────────
cleaned: dict[str, list[dict]] = {}
print("\n[1] PRE-PROCESSING")
for name, rows in raw.items():
    cols = _columns(rows)
    nans = nan_stats(rows, cols)
    null_cols = [c for c, r in nans.items() if r >= 1.0]
    rows2, kept, dropped = _drop_all_null_columns(rows, cols)
    deduped, removed = remove_exact_duplicate_rows(rows2)
    merges = find_column_merge_candidates(deduped, kept)
    cleaned[name] = deduped
    print(f"  {name:12s} rows {len(rows)}->{len(deduped)} (dup -{removed}) | "
          f"null-cols dropped {dropped or '-'} | merge-candidates "
          f"{[(m['column_a'], m['column_b'], m['overlap']) for m in merges] or '-'}")

# Assets-specific assertions
a_raw = raw["Assets"]
a_cols = _columns(a_raw)
check("Assets: 'notes_blank' is 100% empty", nan_stats(a_raw, a_cols).get("notes_blank") == 1.0)
check("Assets: 'status' has exactly one NaN",
      round(nan_stats(a_raw, a_cols).get("status", 0) * len(a_raw)) == 1)
check("Assets: exact duplicate row removed (9 -> 8)", len(cleaned["Assets"]) == 8)
a_kept = _columns(cleaned["Assets"])
a_merges = find_column_merge_candidates(cleaned["Assets"], a_kept)
# CHALLENGE (2): ID and ASSETNUM hold the SAME values under different names -> merge
pair = next((m for m in a_merges if {m["column_a"], m["column_b"]} == {"ID", "ASSETNUM"}), None)
check("Assets: ID & ASSETNUM are an auto-merge candidate (same values, different names)",
      pair is not None and pair["overlap"] >= 0.95)
inferred = propose_column_name(["A-001", "A-002", "A-003", "A-004"])
print(f"      -> proposed merged-column name (heuristic, LLM refines): {inferred}")

# ── Challenge (3): same column NAME 'ID', divergent values -> split + propose names ──
print("\n[1b] SAME-NAME / DIVERGENT-VALUE COLUMNS (challenge 3)")
meta_for_split = {n: {"rows": r, "columns": _columns(r)} for n, r in cleaned.items()}
splits = find_same_name_divergent_columns(meta_for_split)
for s in splits:
    print(f"  '{s['column_name']}' in {s['table_a']} vs {s['table_b']} | overlap {s['overlap']} "
          f"-> SPLIT; propose {s['proposed_name_a']} / {s['proposed_name_b']}")
split_pairs = {(s["column_name"], frozenset([s["table_a"], s["table_b"]])) for s in splits}
check("'ID' in Assets vs WorkOrders flagged divergent (asset ids vs work-order ids -> split)",
      ("ID", frozenset(["Assets", "WorkOrders"])) in split_pairs)
check("'ID' in Assets vs work_tasks NOT split (same entity, overlapping values)",
      ("ID", frozenset(["Assets", "work_tasks"])) not in split_pairs)

# ── Stage 2: unique-table identification ─────────────────────────────────────
print("\n[2] UNIQUE-TABLE IDENTIFICATION")
metas = [build_table_metadata(n, rows, _columns(rows)) for n, rows in cleaned.items()]
report = identify_unique_tables(metas)
for m in metas:
    print(f"  {m['table']:12s} pk={m['primary_key']} ({m['primary_key_kind']}) cols={m['column_count']}")
print("  auto-consolidated:", report["auto_consolidated"] or "-")
print("  candidates:", [(c["tables"], c["metadata_similarity"]) for c in report["candidates"]] or "-")
print("  unique:", report["unique"])

cand_pairs = [set(c["tables"]) for c in report["candidates"]]
check("Assets ~ work_tasks flagged as a consolidation candidate (same entity, different name)",
      {"Assets", "work_tasks"} in cand_pairs)
_apk = next(m for m in metas if m["table"] == "Assets")
check("Assets PK detected as a natural single-column key (ID or ASSETNUM) after dedup",
      _apk["primary_key"] in (["ID"], ["ASSETNUM"]) and _apk["primary_key_kind"] == "natural")
check("Sites / Vendors / Resources stay unique",
      {"Sites", "Vendors", "Resources"} <= set(report["unique"]))

# ── Stage 3: full UDR pipeline (consolidation + classify + FK + tests) ───────
print("\n[3] UDR PIPELINE")
res = run_udr_pipeline(cleaned, run_id="fm-dryrun")
print(f"  tables={res.table_count} columns={res.column_count} relationships={res.relationship_count} "
      f"status={res.status}")
print("  consolidated_groups:", res.consolidated_groups or "-")
print("  unique_table_candidates:",
      [(c["tables"], c["metadata_similarity"]) for c in res.unique_table_candidates] or "-")
for r in res.graph["relationships"]:
    print(f"  FK: {r['src_entity']}.{r['src_column']} -> {r['dst_entity']}.{r['dst_column']} "
          f"(RI {r['confidence']})")
fk_edges = {(r["src_entity"], r["src_column"], r["dst_entity"], r["dst_column"]) for r in res.graph["relationships"]}
# Assets has ID and ASSETNUM as identical columns; the FK resolves to whichever
# is chosen as the Assets primary key (the picker prefers the 'id' name).
check("WorkOrders.asset_no -> Assets PK (ID/ASSETNUM) detected as a foreign key",
      ("WorkOrders", "asset_no", "Assets", "ASSETNUM") in fk_edges
      or ("WorkOrders", "asset_no", "Assets", "ID") in fk_edges)
check("pipeline surfaced the Assets~work_tasks candidate",
      any({"Assets", "work_tasks"} == set(c["tables"]) for c in res.unique_table_candidates))

# ── Stage 4: FM-ontology Step-1b resolution of the FM-native column names ────
print("\n[4] FM ONTOLOGY (deterministic Step-1b column resolution)")
to_resolve = ["ASSETNUM", "EQKTX", "manufacturer", "model", "WONUM", "asset_no",
              "fault_description", "wo_status", "priority", "date_raised",
              "vendor_name", "full_name", "site_ref"]
for col in to_resolve:
    canon, conf = FM.fm_field_lookup(col)
    print(f"  {col:18s} -> {canon if canon else '(no FM alias)'}"
          + (f"  [{conf}]" if canon else ""))
check("ASSETNUM -> asset_code", FM.fm_field_lookup("ASSETNUM")[0] == "asset_code")
check("WONUM -> wo_code", FM.fm_field_lookup("WONUM")[0] == "wo_code")
check("EQKTX -> asset_name", FM.fm_field_lookup("EQKTX")[0] == "asset_name")
check("fault_description -> wo_description", FM.fm_field_lookup("fault_description")[0] == "wo_description")
check("vendor_name -> supplier", FM.fm_field_lookup("vendor_name")[0] == "supplier")

print("\n[5] PRE-PROCESSING SUMMARY (activity-log style, AC7)")
summary = preprocessing_summary(
    nan_removed=int(round(nan_stats(a_raw, a_cols).get("status", 0) * len(a_raw))) + len(a_raw),  # status NaN + the all-null column cells
    duplicate_rows_removed=1,
    columns_merged=0,  # candidates surfaced for confirmation, not auto-merged in this run
    flagged_for_review=len(find_column_merge_candidates(cleaned["Assets"], a_kept))
    + len(report["candidates"]),
)
print("  Assets batch:", summary)
check("summary reports the duplicate row + flagged items",
      summary["duplicate_rows_removed"] == 1 and summary["flagged_for_review"] >= 2)

print("\nALL TESTS PASSED")
