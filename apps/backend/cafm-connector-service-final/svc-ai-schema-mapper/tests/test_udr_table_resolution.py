"""Tests for the B7.1->B12.1 table-resolution report builder.

Pure (no DB/LLM) — mirrors the Migration-Analysis sample: 5 distinct source tables
(qatty, Sites, WorkOrders, Vendors, Resources) routed to canonical UDR tables via
exact (Sites/Vendors/WorkOrders), RAG/alias (Resources->technicians) and semantic
(qatty->assets). Run: python tests/test_udr_table_resolution.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.primitives import build_table_metadata, primary_key_report  # noqa: E402
from udr.table_resolution import (  # noqa: E402
    build_table_resolution,
    classify_table_method,
)


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


# ── fixture: 5 distinct source tables, FM-native column names ────────────────
TABLES = {
    "qatty": [
        {"tag_id": "A-001", "make": "Siemens", "product_code": "AHU-9000"},
        {"tag_id": "A-002", "make": "Trane", "product_code": "RTAC-140"},
        {"tag_id": "A-003", "make": "Otis", "product_code": "Gen2"},
    ],
    "Sites": [
        {"site_ref": "S-01", "city": "London", "postcode": "EC2M 4NR"},
        {"site_ref": "S-02", "city": "Manchester", "postcode": "M1 2WD"},
        {"site_ref": "S-03", "city": "Liverpool", "postcode": "L3 4BL"},
    ],
    "WorkOrders": [
        # asset_no + priority repeat (they are FK / categorical, not keys) so only WONUM
        # qualifies as the natural PK — matching real work-order data.
        {"WONUM": "W-1001", "asset_no": "A-001", "priority": "P2"},
        {"WONUM": "W-1002", "asset_no": "A-001", "priority": "P1"},
        {"WONUM": "W-1003", "asset_no": "A-002", "priority": "P1"},
    ],
    "Vendors": [
        {"vendor_id": "V-01", "vendor_name": "Apex Lifts", "trade": "Lift"},
        {"vendor_id": "V-02", "vendor_name": "ColdChain HVAC", "trade": "Mechanical"},
        {"vendor_id": "V-03", "vendor_name": "SafeFire", "trade": "Fire"},
    ],
    "Resources": [
        {"engineer_id": "E-01", "full_name": "Sam Patel", "site_ref": "S-01"},
        {"engineer_id": "E-02", "full_name": "Joanne Clarke", "site_ref": "S-02"},
        {"engineer_id": "E-03", "full_name": "Marcus Reid", "site_ref": "S-01"},
    ],
}

DEST = {
    "qatty": "assets",
    "Sites": "sites",
    "WorkOrders": "work_orders",
    "Vendors": "vendors",
    "Resources": "technicians",
}
CONF = {"qatty": 0.85}


def _fake_alias_resolver(name):
    """Stand-in for matchers.fm_ontology.fm_table_lookup — only 'Resources' has an alias."""
    return ("technician", 0.96) if str(name).lower().rstrip("s") == "resource" else (None, 0.0)


metas = [build_table_metadata(n, rows, _cols(rows)) for n, rows in TABLES.items()]
report = build_table_resolution(
    TABLES,
    table_metas=metas,
    unique_report={"auto_consolidated": [], "candidates": [], "unique": list(TABLES)},
    dest_table_by_source=DEST,
    confidence_by_source=CONF,
    alias_resolver=_fake_alias_resolver,
)

# ── B7.1 — metadata cards ────────────────────────────────────────────────────
print("\n[B7.1] metadata cards")
check("one card per source table", len(report["metadata_cards"]) == 5)
qatty_card = next(c for c in report["metadata_cards"] if c["table"] == "qatty")
check("qatty card PK tag_id", qatty_card["primary_key"] == ["tag_id"])
check("qatty card column_count 3", qatty_card["column_count"] == 3)
check("qatty card carries sample values", qatty_card["samples"][0]["values"][:1] == ["A-001"])

# ── B7.1 — pairwise verdict ──────────────────────────────────────────────────
print("\n[B7.1] pairwise verdict")
pw = report["pairwise"]
check("a highest pair is reported", pw["highest_pair"] is not None)
check("nothing auto-consolidated (all distinct)", pw["auto_consolidated"] == [])
check("verdict mentions kept distinct", "distinct" in pw["verdict"].lower())

# ── B8.1 — primary-key detection ─────────────────────────────────────────────
print("\n[B8.1] primary-key detection")
pk_by_table = {r["table"]: r for r in report["pk_detection"]}
check("WorkOrders PK = WONUM, natural, 100%/0%",
      pk_by_table["WorkOrders"]["primary_key"] == ["WONUM"]
      and pk_by_table["WorkOrders"]["kind"] == "natural"
      and pk_by_table["WorkOrders"]["uniqueness"] == 1.0
      and pk_by_table["WorkOrders"]["null_rate"] == 0.0)
check("Resources PK = engineer_id", pk_by_table["Resources"]["primary_key"] == ["engineer_id"])
# tie-break: single qualifying candidate -> 'only candidate'
check("Sites tie-break reported", pk_by_table["Sites"]["tie_break"] in
      ("only candidate", "name rank *_id", "name rank code/key/no", "first qualifying column"))

# ── B9.1 — deterministic table mapping ───────────────────────────────────────
print("\n[B9.1] deterministic table mapping")
det = {r["source"]: r for r in report["deterministic"]}
check("Sites exact -> sites @ 1.0", det["Sites"]["method"] == "exact" and det["Sites"]["destination"] == "sites"
      and det["Sites"]["confidence"] == 1.0)
check("Vendors exact -> vendors", det["Vendors"]["method"] == "exact")
check("WorkOrders exact -> work_orders", det["WorkOrders"]["method"] == "exact"
      and det["WorkOrders"]["destination"] == "work_orders")
check("Resources falls through deterministic (method none)", det["Resources"]["method"] == "none")
check("qatty falls through deterministic (method none)", det["qatty"]["method"] == "none")

# ── B10.1 — RAG / alias mapping ──────────────────────────────────────────────
print("\n[B10.1] RAG / alias mapping")
rag = {r["source"]: r for r in report["rag_alias"]}
check("Resources RAG alias hit -> technicians @ 0.96",
      rag["Resources"]["alias_hit"] == "technician"
      and rag["Resources"]["destination"] == "technicians"
      and round(rag["Resources"]["confidence"], 2) == 0.96
      and rag["Resources"]["alias_source"] == "FM_TABLE_SYNONYMS")
check("qatty has no alias (falls to semantic)", rag["qatty"]["alias_hit"] is None)
check("Sites/Vendors/WorkOrders not listed in RAG (already deterministic)",
      "Sites" not in rag and "Vendors" not in rag and "WorkOrders" not in rag)

# ── B11.1 — semantic table mapping ───────────────────────────────────────────
print("\n[B11.1] semantic table mapping")
sem = {r["source"]: r for r in report["semantic"]}
check("only qatty reaches semantic", list(sem) == ["qatty"])
check("qatty semantic -> assets @ 0.85, suggested band",
      sem["qatty"]["destination"] == "assets" and sem["qatty"]["confidence"] == 0.85
      and sem["qatty"]["band"] == "suggested")
check("qatty semantic signal carries column names", "tag_id" in sem["qatty"]["signal"])

# ── B12.1 — final decisions ──────────────────────────────────────────────────
print("\n[B12.1] final decisions")
fin = {r["source"]: r for r in report["final_decisions"]}
check("5 final decisions", len(fin) == 5)
check("Sites exact 1.0", fin["Sites"]["method"] == "exact" and fin["Sites"]["confidence"] == 1.0)
check("Resources RAG/alias 0.96", fin["Resources"]["method"] == "RAG/alias")
check("qatty semantic LLM 0.85", fin["qatty"]["method"] == "semantic LLM" and fin["qatty"]["confidence"] == 0.85)

# ── classify_table_method unit checks ────────────────────────────────────────
print("\n[unit] classify_table_method")
check("exact plural-normalise", classify_table_method("Sites", "sites")["method"] == "exact")
check("levenshtein within 2",
      classify_table_method("work_order", "work_orders")["method"] in ("exact", "levenshtein"))
check("no resolver -> semantic fallback",
      classify_table_method("qatty", "assets")["method"] == "semantic")
check("unresolved when no destination",
      classify_table_method("qatty", None)["method"] == "unresolved")

# ── primary_key_report diagnostics ───────────────────────────────────────────
print("\n[unit] primary_key_report")
rep = primary_key_report(TABLES["qatty"], _cols(TABLES["qatty"]))
check("qatty natural PK tag_id with confidence 1.0",
      rep["kind"] == "natural" and rep["columns"] == ["tag_id"] and rep["confidence"] == 1.0)

print("\nALL TESTS PASSED")
