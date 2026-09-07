"""Tests for the FM ontology (semantic Step-1b RAG/alias + FM persona). Pure.
Run: python tests/test_fm_ontology.py"""
import importlib
import os
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
# Stub the 'matchers' package so its heavy __init__ (pulls cafm_shared) doesn't run, while
# submodule relative imports (cmms_aliases -> .fm_ontology) still resolve.
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

F = importlib.import_module("matchers.fm_ontology")
_cmms = importlib.import_module("matchers.cmms_aliases")
CANONICAL_FIELDS, CMMS_ALIASES, get_cmms_alias = (
    _cmms.CANONICAL_FIELDS, _cmms.CMMS_ALIASES, _cmms.get_cmms_alias,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── FM column synonyms resolve to canonical fields ───────────────────────────
check("assetnum -> asset_code", F.fm_field_lookup("assetnum")[0] == "asset_code")
check("EQUNR -> asset_code", F.fm_field_lookup("EQUNR")[0] == "asset_code")
check("wonum -> wo_code", F.fm_field_lookup("wonum")[0] == "wo_code")
check("fault_description -> wo_description", F.fm_field_lookup("fault_description")[0] == "wo_description")
check("manufacturer -> make", F.fm_field_lookup("manufacturer")[0] == "make")
check("EQKTX -> asset_name", F.fm_field_lookup("eqktx")[0] == "asset_name")
check("targcompdate -> due_date", F.fm_field_lookup("targcompdate")[0] == "due_date")
check("unknown field -> None", F.fm_field_lookup("totally_unknown_xyz")[0] is None)
check("fm hit confidence 0.96", F.fm_field_lookup("assetnum")[1] == F.FM_ALIAS_CONFIDENCE == 0.96)

# every FM field alias targets a REAL canonical field (so the mapper can confirm it)
check("all FM field aliases map to a canonical field",
      all(v in CANONICAL_FIELDS for v in F.FM_FIELD_ALIASES.values()))

# ── FM entity (table) recognition ────────────────────────────────────────────
check("job -> work_order", F.fm_table_lookup("job")[0] == "work_order")
check("property -> site", F.fm_table_lookup("property")[0] == "site")
check("ppm -> ppm_schedule", F.fm_table_lookup("ppm")[0] == "ppm_schedule")
check("subcontractor -> vendor", F.fm_table_lookup("subcontractor")[0] == "vendor")
check("non-entity -> None", F.fm_table_lookup("zzz_nope")[0] is None)

# ── equipment taxonomy (asset-type value recognition) ────────────────────────
check("AHU -> air_handling_unit", F.fm_equipment_lookup("AHU")[0] == "air_handling_unit")
check("chiller -> chiller", F.fm_equipment_lookup("chiller")[0] == "chiller")
check("FACP -> fire_alarm_panel", F.fm_equipment_lookup("FACP")[0] == "fire_alarm_panel")
check("passenger lift -> lift", F.fm_equipment_lookup("passenger lift")[0] == "lift")

# ── controlled value vocabularies ────────────────────────────────────────────
check("'In Progress' status", F.normalize_status("In Progress") == "in_progress")
check("'Closed' -> completed", F.normalize_status("Closed") == "completed")
check("'Raised' -> open", F.normalize_status("Raised") == "open")
check("'Critical' priority -> P1", F.normalize_priority("Critical") == "P1")
check("'Routine' -> P3", F.normalize_priority("Routine") == "P3")
check("'Breakdown' wo type -> reactive", F.normalize_wo_type("Breakdown") == "reactive")
check("'PPM' wo type -> planned", F.normalize_wo_type("PPM") == "planned")

# ── FM personality ───────────────────────────────────────────────────────────
check("FM_PERSONA mentions FM domain", "facilities-management" in F.FM_PERSONA
      and "COBie" in F.FM_PERSONA and "SFG20" in F.FM_PERSONA)

# ── merge into CMMS_ALIASES (deterministic alias step now resolves FM terms) ──
check("CMMS_ALIASES now resolves FM-only term eqktx",
      get_cmms_alias("eqktx") is not None and get_cmms_alias("eqktx")[0] == "asset_name")
check("CMMS_ALIASES resolves installation_ref -> asset_code",
      get_cmms_alias("installation_ref")[0] == "asset_code")
check("existing CMMS alias not overwritten (assetnum still asset_code)",
      get_cmms_alias("assetnum")[0] == "asset_code")
check("FM merge added entries", len(CMMS_ALIASES) > 299)

# ── table routing suggestions for unmatched / low-confidence tables (7.4 AC6) ─
CANON = ["assets", "sites", "work_orders", "vendors"]
# 1. confident existing match -> assign it
r1 = F.suggest_table_routing("assets", CANON, matched="assets", confidence=1.0)
check("confident match -> assign", r1["action"] == "assign" and r1["target"] == "assets")
# 2. low confidence + FM entity + an existing dest table fits -> assign that table (suggestion)
r2 = F.suggest_table_routing("Resources", CANON + ["resources"], matched=None, confidence=0.2)
check("Resources -> assign existing 'resources' (FM entity match)",
      r2["action"] == "assign" and r2["target"] == "resources" and "fm_ontology" in r2["reason"])
# 3. low confidence + FM entity + NO dest table -> suggest CREATING one named for the entity
r3 = F.suggest_table_routing("Resources", CANON, matched=None, confidence=0.2)
check("Resources, no dest table -> suggest create 'resource'",
      r3["action"] == "create" and r3["suggested_new_name"] == "resource")
# 4. an unknown sheet with no FM entity -> create new table named after the source
r4 = F.suggest_table_routing("misc_legacy_sheet", CANON, matched=None, confidence=0.1)
check("unknown sheet -> suggest create from source name",
      r4["action"] == "create" and r4["suggested_new_name"] == "misc_legacy_sheet")
# 5. a 'job_cards' sheet -> FM entity work_order -> assign existing work_orders table
r5 = F.suggest_table_routing("job_cards", CANON, matched=None, confidence=0.3)
check("job_cards -> assign work_orders (FM entity)",
      r5["action"] == "assign" and r5["target"] == "work_orders")

# ── RAG reference doc is locatable + readable ────────────────────────────────
txt = F.fm_reference_text()
check("RAG reference doc found + read", isinstance(txt, str) and "FM ONTOLOGY SEMANTIC MAPPING" in txt)

print("\nALL TESTS PASSED")
