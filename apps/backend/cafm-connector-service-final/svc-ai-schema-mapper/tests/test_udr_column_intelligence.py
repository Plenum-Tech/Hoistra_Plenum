"""Tests for the B13.1->B21.1 column-intelligence report builder, over the real fixture.

Run: python tests/test_udr_column_intelligence.py
"""
import importlib
import os
import sys
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import detect_primary_key  # noqa: E402
from udr.column_intelligence import (  # noqa: E402
    _norm_col_name,
    build_column_intelligence,
    canonical_name_for_group,
)

# fm_ontology via a stub 'matchers' package (skip the heavy __init__).
_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg
FM = importlib.import_module("matchers.fm_ontology")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "fm_source_primary.xlsx")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def _cols(rows):
    seen, out = [], []
    for r in rows:
        for k in r:
            if k not in seen:
                seen.append(k); out.append(k)
    return out


sheets = pd.read_excel(FIX, sheet_name=None)
raw = {n: df.astype(object).where(pd.notna(df), None).to_dict(orient="records") for n, df in sheets.items()}
tables = {
    n: {"rows": rows, "columns": _cols(rows), "pk": detect_primary_key(rows, _cols(rows)).get("columns", [])}
    for n, rows in raw.items()
}

# A plausible destination routing (source table -> canonical) + per-column dest guesses.
DEST_TABLE = {"Assets": "assets", "Sites": "sites", "WorkOrders": "work_orders", "Vendors": "vendors", "Resources": "technicians"}
DEST_COL = {}
CONF = {}
for t, meta in tables.items():
    for c in meta["columns"]:
        canon, conf = FM.fm_field_lookup(c)
        if canon:
            DEST_COL[(t, c)] = canon
            CONF[(t, c)] = conf

report = build_column_intelligence(
    tables,
    dest_table_by_source=DEST_TABLE,
    dest_col_by_source_col=DEST_COL,
    conf_by_source_col=CONF,
    field_resolver=FM.fm_field_lookup,
)

ncols = sum(len(m["columns"]) for m in tables.values())

# ── B13.1 prefixing ──────────────────────────────────────────────────────────
print("\n[B13.1] prefixing")
check("one prefixing row per column", len(report["prefixing"]) == ncols)
_pk0 = report["prefixing"][0]["prefixed_key"]
check("prefixed key is lowercase dot-separated (e.g. 'sites.id')",
      "." in _pk0 and not _pk0.startswith("(") and _pk0.split(".", 1)[0] == _pk0.split(".", 1)[0].lower())
check("prefixing carries destination table", report["prefixing"][0]["dest_table"] in DEST_TABLE.values())

# ── B14.1 metadata ────────────────────────────────────────────────────────────
print("\n[B14.1] metadata")
check("metadata rows present", len(report["metadata"]) > 0)
m0 = report["metadata"][0]
check("metadata has the 4 dimensions + samples + canonical_name (5th)",
      {"dest_table", "classification", "format", "samples", "canonical_name"} <= set(m0))
check("every metadata row carries a canonical name matching its group",
      all(
          r.get("canonical_name") == report["column_canonical"].get(f"{r['source_table']}.{r['column']}")
          for r in report["metadata"]
      ))
check("classification label is PK/FK/Shared",
      all(r["classification"] in ("PK", "FK", "Shared") for r in report["metadata"]))

# ── B15.1 FK candidates ────────────────────────────────────────────────────────
print("\n[B15.1] FK candidates")
fk = report["fk_candidates"]
print("  FK candidates:", [(f["src_table"], f["src_column"], "->", f["dst_table"], f["dst_column"], f["ri"]) for f in fk])
check("site_ref FK -> Sites.site_ref confirmed at RI 1.0",
      any(f["src_column"] == "site_ref" and f["dst_table"] == "Sites" and f["confirmed"] for f in fk))

# ── B17.1 / B18.1 gates ─────────────────────────────────────────────────────────
print("\n[B17.1/B18.1] gates")
check("format pairs scored", report["summary"]["format_pairs_scored"] > 0)
check("every shown format pair has a boolean pass", all(isinstance(p["pass"], bool) for p in report["format_gate"]))
check("value-pattern rows carry a decision",
      all(p["decision"] in ("group together", "separate") for p in report["value_pattern"]))
check("within-table similarity persisted",
      isinstance(report.get("within_table_similarity"), dict))
check("within-table format pairs carry score_pct",
      all(isinstance(p.get("score_pct"), int) for p in report["within_table_similarity"].get("format_gate", [])))
check("value-pattern groups persisted",
      isinstance(report.get("value_pattern_groups"), dict))
check("cross-table value-shape groups have value_shape + columns",
      all("value_shape" in g and g.get("columns")
          for g in report.get("value_pattern_groups", {}).get("cross_table", [])))

# ── B19.1 grouping ──────────────────────────────────────────────────────────────
print("\n[B19.1] grouping")
groups = report["groups"]
print("  groups:", [(g["group_id"], g["canonical_name"], len(g["members"])) for g in groups])
check("multi-member groups detected", len(groups) >= 1)
check("every group has a canonical name + members", all(g["canonical_name"] and g["members"] for g in groups))
# "joined via" provenance: every member carries joined_by ∈ {name, value}, and a member
# whose name matches the canonical is flagged 'name' (the rest joined by value/format).
check("every member_class carries joined_by in {name, value}",
      all(mc.get("joined_by") in ("name", "value")
          for g in groups for mc in g.get("member_classes", [])))
check("name-matching members are flagged joined_by=name (else value)",
      all(
          mc["joined_by"] == ("name" if _norm_col_name(mk["column"]) == _norm_col_name(g["canonical_name"])
                              else "value")
          for g in groups
          for mk, mc in zip(g["member_keys"], g.get("member_classes", []))
      ))

# ── unified names invariant: same canonical across a whole group ───────────────
# Within-table collisions intentionally revert the loser to its source name — e.g.
# Assets carries BOTH ID and ASSETNUM as asset codes, but they can't both become
# 'asset_code' in the destination, so one is suppressed and keeps its original name.
# The invariant therefore holds across the NON-suppressed members of each group.
print("\n[unified] canonical-name invariant")
_suppressed = {
    f"{m['source_table']}.{m['column']}" for m in report["metadata"] if m.get("canonical_suppressed")
}
for g in groups:
    names = {
        report["column_canonical"][k["table"] + "." + k["column"]]
        for k in g["member_keys"]
        if f"{k['table']}.{k['column']}" not in _suppressed
    }
    check(f"group {g['group_id']} non-suppressed members resolve to ONE canonical name ({names})",
          len(names) == 1)

# ── B20.1 classification ────────────────────────────────────────────────────────
print("\n[B20.1] classification")
check("classification rows present with verdicts",
      len(report["classification"]) == len(groups)
      and all(r["verdict"] for r in report["classification"]))

# ── B21.1 dest mapping ──────────────────────────────────────────────────────────
print("\n[B21.1] destination mapping")
check("one dest-mapping row per column", len(report["dest_mapping"]) == ncols)
check("FM-resolved columns are matched with confidence",
      any(r["matched_column"] and isinstance(r["confidence"], (int, float)) for r in report["dest_mapping"]))

# ── B21.1 dest-column EXISTENCE guard ─────────────────────────────────────────────
# A name-only / LLM match onto a column the routed table doesn't have (e.g. an
# 'asset id' column on a 'sites' sheet mapped to canonical 'asset_code', but
# plenum_cafm.sites has no asset_code column) must be a NEW column, never auto-resolve.
print("\n[B21.1] dest-column existence guard")
_guard_tables = {
    "Sites": {"rows": [{"id": f"S-0{i}", "asset id": f"A-00{i}", "site_name": f"Site {i}"}
                       for i in range(1, 4)],
              "columns": ["id", "asset id", "site_name"]},
}
_guard = build_column_intelligence(
    _guard_tables,
    dest_table_by_source={"Sites": "sites"},
    dest_col_by_source_col={("Sites", "asset id"): "asset_code",
                            ("Sites", "site_name"): "site_name", ("Sites", "id"): "id"},
    conf_by_source_col={("Sites", "asset id"): 0.95,
                        ("Sites", "site_name"): 0.98, ("Sites", "id"): 0.98},
    dest_columns_by_table={"sites": {"id", "site_id", "site_code", "site_name", "city", "postcode"}},
)
_g = {r["source"]: r for r in _guard["dest_mapping"]}
check("match onto a non-existent dest column ('sites'.asset_code) ⇒ new column",
      _g["sites.asset id"]["outcome"] == "new column" and _g["sites.asset id"]["matched_column"] is None)
check("matches onto REAL dest columns still resolve",
      _g["sites.site_name"]["matched_column"] == "site_name"
      and _g["sites.id"]["matched_column"] == "id")

# ── B21.1 canonical-driven resolution ─────────────────────────────────────────────
# An unresolved column whose CANONICAL name (from cross-table grouping) is a real column on
# the routed table must resolve to it — e.g. works.property_ref grouped to canonical 'site_id'
# and 'assets' has a site_id column. The deterministic mapper left it unresolved (no dest_col).
print("\n[B21.1] canonical-driven dest resolution")
_cd_tables = {
    "Sites": {"rows": [{"id": f"S-0{i}"} for i in range(1, 4)], "columns": ["id"]},
    "works": {"rows": [{"property_ref": f"S-0{i}"} for i in range(1, 4)], "columns": ["property_ref"]},
}
# 'assets' offers a column named after property_ref's canonical; deterministic mapper left
# property_ref UNRESOLVED (only Sites.id is in dest_col_by_source_col).
_cd = build_column_intelligence(
    _cd_tables,
    dest_table_by_source={"Sites": "sites", "works": "assets"},
    dest_col_by_source_col={("Sites", "id"): "id"},   # property_ref intentionally UNRESOLVED
    conf_by_source_col={("Sites", "id"): 0.98},
    dest_columns_by_table={"sites": {"id", "site_id"},
                           "assets": {"id", "site_id", "asset_name"}},
)
_cdm = {r["source"]: r for r in _cd["dest_mapping"]}
_pr = _cdm["works.property_ref"]
_pr_canon = _cd["column_canonical"].get("works.property_ref")
# The mechanism: an unresolved column whose CANONICAL name is a real column on the routed
# table resolves to it (not a false "new column"). Assert against the computed canonical so
# the test isn't tied to PK-qualification naming nuances.
check(f"unresolved property_ref (canonical {_pr_canon!r}) is a real assets column",
      _pr_canon in {"id", "site_id", "asset_name"})
check("unresolved property_ref resolves to its canonical's real dest column (not new column)",
      _pr["matched_column"] is not None and _pr["outcome"] != "new column")

# ── B21 mirrors the gate's unresolved SUGGESTIONS (lower priority than canonical-driven) ──
# An unresolved column the gate shows a best-guess suggestion for (wo_status → status) must read
# the SAME in B21, not 'new column'. But the suggestion is a LOWER-priority fallback: a strong
# canonical-driven match (property_ref → site_id) must still beat a weak fuzzy suggestion
# (property_ref → document_ids).
print("\n[B21.1] mirrors unresolved suggestions, below canonical-driven resolution")
_us_tables = {
    "WorkOrders": {"rows": [{"id": f"W-100{i}", "wo_status": "Open"} for i in range(1, 4)],
                   "columns": ["id", "wo_status"], "pk": ["id"]},
    "works": {"rows": [{"id": f"A-00{i}", "property_ref": f"S-0{i}"} for i in range(1, 4)],
              "columns": ["id", "property_ref"], "pk": ["id"]},
    "Sites": {"rows": [{"id": f"S-0{i}"} for i in range(1, 4)], "columns": ["id"], "pk": ["id"]},
}
_us = build_column_intelligence(
    _us_tables,
    dest_table_by_source={"WorkOrders": "work_orders", "works": "assets", "Sites": "sites"},
    dest_col_by_source_col={("WorkOrders", "id"): "id", ("works", "id"): "id", ("Sites", "id"): "id"},
    conf_by_source_col={("WorkOrders", "id"): 0.98, ("works", "id"): 0.98, ("Sites", "id"): 0.98},
    dest_columns_by_table={"work_orders": {"id", "status"}, "assets": {"id", "site_id", "document_ids"}, "sites": {"id", "site_id"}},
    # the gate's best-guess suggestions for the unresolved columns:
    unresolved_suggest_by_source_col={("WorkOrders", "wo_status"): "status", ("works", "property_ref"): "document_ids"},
)
_usm = {r["source"]: r for r in _us["dest_mapping"]}
check("unresolved wo_status mirrors the gate suggestion → status (not 'new column')",
      _usm["workorders.wo_status"]["matched_column"] == "status"
      and _usm["workorders.wo_status"]["outcome"] != "new column")
check("a strong canonical match (property_ref → site_id) still beats the weak suggestion (document_ids)",
      _usm["works.property_ref"]["matched_column"] == "site_id")

# ── Single mapped column keeps its SOURCE name as canonical (not the dest column) ──
# A lone source column mapped to a differently-named dest column must NOT be renamed to the
# destination in the canonical display ('product_name' mapped to 'asset_name' still reads as
# 'product_name'); the mapping is shown separately via matched_column.
print("\n[canonical] single mapped column keeps source name")
_sn_tables = {
    "works": {"rows": [{"product_name": f"P{i}", "vendor_name": f"V{i}"} for i in range(1, 4)],
              "columns": ["product_name", "vendor_name"]},
}
_sn = build_column_intelligence(
    _sn_tables,
    dest_table_by_source={"works": "assets"},
    dest_col_by_source_col={("works", "product_name"): "asset_name"},  # mapped to a DIFFERENT name
    conf_by_source_col={("works", "product_name"): 0.96},
    dest_columns_by_table={"assets": {"asset_name", "id"}},
)
_sn_cc = _sn["column_canonical"]
check("single column canonical = source name, not the destination column",
      _sn_cc.get("works.product_name") == "product_name")
_sn_dm = {r["source"]: r for r in _sn["dest_mapping"]}
check("…while still mapping to the destination column (shown separately)",
      _sn_dm["works.product_name"]["matched_column"] == "asset_name"
      and _sn_dm["works.product_name"]["outcome"] != "new column")

# ── B21 displays the SOURCE column name (no auto-rename), even for grouped/aliased cols ──
print("\n[B21] displayed identity stays the source column")
_d_tables = {
    "works": {"rows": [{"property_ref": f"S-0{i}", "product code": f"C{i}"} for i in range(1, 4)],
              "columns": ["property_ref", "product code"]},
    "Sites": {"rows": [{"id": f"S-0{i}"} for i in range(1, 4)], "columns": ["id"]},
}
_d = build_column_intelligence(
    _d_tables,
    dest_table_by_source={"works": "assets", "Sites": "sites"},
    # property_ref grouped to canonical site_id; product code aliased to asset_code (weak name)
    dest_col_by_source_col={("works", "product code"): "asset_code"},
    conf_by_source_col={("works", "product code"): 0.96},
    dest_columns_by_table={"assets": {"asset_code", "site_id", "id"}, "sites": {"id", "site_id"}},
)
_dm = {r["source"]: r for r in _d["dest_mapping"]}
check("grouped column displays its SOURCE name, not the unified canonical (property_ref, not site_id)",
      _dm["works.property_ref"]["canonical_source"] == "works.property_ref"
      and _dm["works.property_ref"]["canonical_name"] == "property_ref")
check("a curated/aliased mapping with weak name + inconclusive value is NOT demoted",
      _dm["works.product code"]["matched_column"] == "asset_code"
      and _dm["works.product code"]["outcome"] != "new column")

# ── Group canonical (auto + pin) skips a cross-entity member ──────────────────────
# A column whose VALUES are asset codes (A-###) but whose destination is a different entity's
# PK must be flagged a NEW column, not merged. vendors.id holds A-### (it value-joins the asset
# group → canonical 'asset_id'), but the dest is the vendor PK (a UUID): asset codes don't
# belong there → new column, shown under what the values actually are (asset_id).
print("\n[canonical] cross-entity id column ⇒ new column (not merged into the other entity's PK)")
_ce_tables = {
    "Vendors": {"rows": [{"id": f"A-00{i}"} for i in range(1, 4)], "columns": ["id"], "pk": ["id"]},
    "works": {"rows": [{"id": f"A-00{i}", "tagnum": f"A-00{i}"} for i in range(1, 9)],
              "columns": ["id", "tagnum"], "pk": ["id"]},
}
_ce = build_column_intelligence(
    _ce_tables,
    dest_table_by_source={"Vendors": "vendors", "works": "assets"},
    dest_col_by_source_col={("Vendors", "id"): "id", ("works", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.96, ("works", "id"): 0.96},
    # dest vendors.id / assets.id are UUID PKs — no contrasting sample values supplied.
    dest_columns_by_table={"vendors": {"id", "vendor_name"}, "assets": {"id", "asset_code"}},
)
_ce_dm = {r["source"]: r for r in _ce["dest_mapping"]}
check("vendors.id (asset codes) ⇒ new column, NOT merged into the vendor PK",
      _ce_dm["vendors.id"]["matched_column"] is None
      and _ce_dm["vendors.id"]["outcome"] == "new column")
check("…and the new column is shown under what the values are (asset_id), not the source 'id'",
      _ce_dm["vendors.id"]["canonical_source"] == "vendors.asset_id")
check("a same-entity asset id (works.id → assets.id) still auto-resolves",
      _ce_dm["works.id"]["matched_column"] == "id"
      and _ce_dm["works.id"]["outcome"] != "new column")

# ── Ambiguous multi-PK group: a generic shared dest ('id') must NOT erase the entity ──
# Real-run shape: vendors.id and works.id are BOTH PKs holding A-### codes, but neither's
# value set is a superset of the group union (works is sampled to fewer rows than the asset
# codes referenced elsewhere), so the PK-parent tier is skipped. The only common destination
# column is the generic 'id' — using it as the canonical would make vendors.id look like a
# clean id→id match. The descriptive shared name 'asset_id' (2 members) must win instead, so
# the cross-entity guard still fires and vendors.id → new column.
print("\n[canonical] ambiguous multi-PK group keeps the descriptive name over generic 'id'")
_amb_tables = {
    "Sites": {"rows": [{"id": f"S-0{i}", "asset id": f"A-00{i}"} for i in range(1, 4)],
              "columns": ["id", "asset id"], "pk": ["id"]},
    "Vendors": {"rows": [{"id": f"A-00{i}"} for i in range(1, 4)], "columns": ["id"], "pk": ["id"]},
    # WorkOrders references A-005, which the sampled works.id (A-001..A-004) does NOT contain →
    # no single PK covers the value union → PK-parent tier is skipped.
    "WorkOrders": {"rows": [{"id": f"W-100{i}", "asset id": v}
                            for i, v in enumerate(["A-001", "A-003", "A-002", "A-005"], 1)],
                   "columns": ["id", "asset id"], "pk": ["id"]},
    "works": {"rows": [{"id": f"A-00{i}", "tagnum": f"A-00{i}"} for i in range(1, 5)],
              "columns": ["id", "tagnum"], "pk": ["id"]},
}
_amb = build_column_intelligence(
    _amb_tables,
    dest_table_by_source={"Sites": "sites", "Vendors": "vendors", "WorkOrders": "workorders", "works": "assets"},
    dest_col_by_source_col={("Vendors", "id"): "id", ("works", "id"): "id", ("WorkOrders", "id"): "id", ("Sites", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.98, ("works", "id"): 0.98, ("WorkOrders", "id"): 0.98, ("Sites", "id"): 0.98},
    dest_columns_by_table={"sites": {"id", "site_id"}, "vendors": {"id", "vendor_name"},
                           "workorders": {"id", "asset_code"}, "assets": {"id", "asset_code"}},
)
_amb_g2 = next((g for g in _amb["groups"] if "vendors.id" in g["members"]), None)
check("the ambiguous asset-code group canonical is 'asset_id', not the generic 'id'",
      _amb_g2 is not None and _amb_g2["canonical_name"] == "asset_id")
_amb_dm = {r["source"]: r for r in _amb["dest_mapping"]}
check("→ vendors.id is flagged a NEW column (asset codes ≠ vendor PK), shown as vendors.asset_id",
      _amb_dm["vendors.id"]["outcome"] == "new column"
      and _amb_dm["vendors.id"]["matched_column"] is None
      and _amb_dm["vendors.id"]["canonical_source"] == "vendors.asset_id")
check("→ works.id (same entity, → assets) still auto-resolves",
      _amb_dm["works.id"]["matched_column"] == "id"
      and _amb_dm["works.id"]["outcome"] != "new column")

# Same ambiguous shape but WITH the real FM resolver active. FM maps asset_id → 'id' (the assets
# table's own PK), which previously collapsed the descriptive group name back to the generic 'id'
# AFTER it was correctly chosen — silently re-breaking the cross-entity guard on every live run.
# The FM canonical must NOT collapse a descriptive name to a generic PK name.
print("\n[canonical] FM (asset_id→id) must not collapse the descriptive group name")
assert FM.fm_field_lookup("asset_id")[0] == "id", "precondition: FM maps asset_id→id"
_fmc = build_column_intelligence(
    _amb_tables,
    dest_table_by_source={"Sites": "sites", "Vendors": "vendors", "WorkOrders": "workorders", "works": "assets"},
    dest_col_by_source_col={("Vendors", "id"): "id", ("works", "id"): "id", ("WorkOrders", "id"): "id", ("Sites", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.98, ("works", "id"): 0.98, ("WorkOrders", "id"): 0.98, ("Sites", "id"): 0.98},
    field_resolver=FM.fm_field_lookup,
    dest_columns_by_table={"sites": {"id", "site_id"}, "vendors": {"id", "vendor_name"},
                           "workorders": {"id", "asset_code"}, "assets": {"id", "asset_code"}},
)
_fmc_g2 = next((g for g in _fmc["groups"] if "vendors.id" in g["members"]), None)
check("with FM active, the group canonical is still 'asset_id' (not collapsed to 'id')",
      _fmc_g2 is not None and _fmc_g2["canonical_name"] == "asset_id")
_fmc_dm = {r["source"]: r for r in _fmc["dest_mapping"]}
check("with FM active, vendors.id is still a NEW column (cross-entity guard fires)",
      _fmc_dm["vendors.id"]["outcome"] == "new column"
      and _fmc_dm["vendors.id"]["matched_column"] is None)

# ── summary ──────────────────────────────────────────────────────────────────────
print("\n[summary]", report["summary"])
s = report["summary"]
check("summary counts columns + groups",
      s["columns_analyzed"] == ncols and s["groups"] == len(groups)
      and (s["pk_groups"] + s["fk_groups"] + s["shared_groups"]) == len(groups))

# ── canonical_name_for_group unit: PK wins ───────────────────────────────────────
print("\n[unit] canonical_name_for_group")
cls = {("Sites", "site_ref"): {"classification": "PK"}, ("Assets", "site_ref"): {"classification": "FK"}}
nm = canonical_name_for_group([("Sites", "site_ref"), ("Assets", "site_ref")], cls,
                              field_resolver=FM.fm_field_lookup)
check("PK name wins (site_ref, no FM alias -> site_ref)", nm == "site_ref")
nm2 = canonical_name_for_group([("WorkOrders", "WONUM")], {("WorkOrders", "WONUM"): {"classification": "PK"}},
                               field_resolver=FM.fm_field_lookup)
check("WONUM PK -> FM canon wo_code", nm2 == "wo_code")

# ── canonical_name_for_group unit: multi-PK parent (value-linked id naming) ───────
# When a shared id value (A-001…) is the PK of MORE THAN ONE table, the group is
# named after the PARENT — the PK whose distinct values contain the others. Here
# Assets.id (A-001..A-005) contains Vendors.id (A-001..A-003), so the asset-id group
# resolves to 'asset_id' (generic 'id' qualified by table), not the ambiguous neutral
# fallback. Mirrors "A001 in another table -> same name across tables".
_pk_tables = {
    "Assets": {"rows": [{"id": f"A-00{i}"} for i in range(1, 6)], "columns": ["id"]},
    "Vendors": {"rows": [{"id": f"A-00{i}"} for i in range(1, 4)], "columns": ["id"]},
}
_pk_cls = {("Assets", "id"): {"classification": "PK"}, ("Vendors", "id"): {"classification": "PK"}}
nm3 = canonical_name_for_group(
    [("Assets", "id"), ("Vendors", "id")], _pk_cls, tables=_pk_tables, field_resolver=None, group_id="G9",
)
check("multi-PK group named after parent PK (Assets.id ⊇ Vendors.id -> asset_id)", nm3 == "asset_id")

# Disjoint multi-PK (no parent) must NOT pick a wrong table's name. With no PK whose
# values cover the group, Tier 1 stays out and naming falls through — here both members
# are literally 'id', so consensus yields the generic 'id', never 'asset_id'/'site_id'.
_disj = {
    "Assets": {"rows": [{"id": f"A-00{i}"} for i in range(1, 4)], "columns": ["id"]},
    "Sites": {"rows": [{"id": f"S-0{i}"} for i in range(1, 4)], "columns": ["id"]},
}
nm4 = canonical_name_for_group(
    [("Assets", "id"), ("Sites", "id")],
    {("Assets", "id"): {"classification": "PK"}, ("Sites", "id"): {"classification": "PK"}},
    tables=_disj, field_resolver=None, group_id="G9",
)
check("disjoint multi-PK picks no parent (stays generic 'id', not asset_id/site_id)",
      nm4 == "id")

# Generic PK is qualified by the DESTINATION entity, not the source table name: the source
# table 'works' maps to destination 'assets', so its PK becomes asset_id (not work_id).
_works = {"works": {"rows": [{"id": f"A-00{i}"} for i in range(1, 4)], "columns": ["id"]}}
nm5 = canonical_name_for_group(
    [("works", "id")], {("works", "id"): {"classification": "PK"}},
    tables=_works, dest_table_by_source={"works": "assets"}, field_resolver=None,
)
check("generic PK uses destination entity (works→assets ⇒ asset_id, not work_id)",
      nm5 == "asset_id")

# A descriptive name the group ALREADY shares (≥2 members) beats qualifying a generic PK by
# table, and does NOT need the destination resolved — at the pre-semantic gate 'works' isn't
# yet mapped to 'assets', yet sites.asset_id + workorders.asset_id must still make the group
# 'asset_id' (not the weaker 'work_id' from works.id's generic PK).
_grp = {
    "works": {"rows": [{"id": f"A-00{i}"} for i in range(1, 6)], "columns": ["id"]},
    "Sites": {"rows": [{"asset_id": f"A-00{i}"} for i in range(1, 4)], "columns": ["asset_id"]},
    "WorkOrders": {"rows": [{"asset_id": f"A-00{i}"} for i in range(1, 4)], "columns": ["asset_id"]},
}
nm6 = canonical_name_for_group(
    [("works", "id"), ("Sites", "asset_id"), ("WorkOrders", "asset_id")],
    {("works", "id"): {"classification": "PK"}},
    tables=_grp, field_resolver=None,  # NB: no dest_table_by_source — the pre-semantic gate
)
check("shared descriptive name beats generic-PK-by-table (no dest ⇒ asset_id, not work_id)",
      nm6 == "asset_id")

# ── B21.1 destination mapping — value-aware gate (name AND value) ─────────────────
print("\n[B21.1] destination mapping value-gate")
_vt = {"Vendors": {"rows": [{"id": "A-001"}, {"id": "A-002"}, {"id": "A-003"}], "columns": ["id"]}}
# Destination vendors.id already holds vendor ids (V-###) that DON'T overlap the source's
# asset codes (A-###) → a name match alone must NOT auto-resolve.
_rep_mismatch = build_column_intelligence(
    _vt, dest_table_by_source={"Vendors": "vendors"},
    dest_col_by_source_col={("Vendors", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.98}, field_resolver=None,
    dest_samples_by_table={"vendors": {"id": ["V-001", "V-002", "V-003"]}},
)
_r = next(r for r in _rep_mismatch["dest_mapping"] if r["source"] == "vendors.id")
check("value mismatch (A-### vs V-###) ⇒ outcome 'new column'", _r["outcome"] == "new column")
check("value mismatch row carries the score breakdown", _r["scores"] and _r["scores"]["value"] == 0.0)
# Same names AND overlapping values → auto-resolve.
_rep_match = build_column_intelligence(
    _vt, dest_table_by_source={"Vendors": "vendors"},
    dest_col_by_source_col={("Vendors", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.98}, field_resolver=None,
    dest_samples_by_table={"vendors": {"id": ["A-001", "A-002", "A-003"]}},
)
_r2 = next(r for r in _rep_match["dest_mapping"] if r["source"] == "vendors.id")
check("value match ⇒ outcome 'auto-resolved'", _r2["outcome"] == "auto-resolved")
# No destination samples → fail-safe to the upstream confidence band (unchanged).
_rep_nodest = build_column_intelligence(
    _vt, dest_table_by_source={"Vendors": "vendors"},
    dest_col_by_source_col={("Vendors", "id"): "id"},
    conf_by_source_col={("Vendors", "id"): 0.98}, field_resolver=None,
)
_r3 = next(r for r in _rep_nodest["dest_mapping"] if r["source"] == "vendors.id")
check("no dest samples ⇒ fail-safe auto-resolved (unchanged), no scores",
      _r3["outcome"] == "auto-resolved" and _r3["scores"] is None)

print("\nALL TESTS PASSED")
