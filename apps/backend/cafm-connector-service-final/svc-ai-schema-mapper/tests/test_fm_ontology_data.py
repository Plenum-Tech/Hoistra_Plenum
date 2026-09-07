"""Tests for the docx-derived FM ontology aliases (fm_ontology_data.py), merged into
fm_ontology + cmms_aliases. Verifies the per-platform CMMS/CAFM/EAM vocabulary from
FM_Ontology_Reference_v1.0.docx resolves to canonical fields/entities. Pure.
Run: python tests/test_fm_ontology_data.py"""
import importlib
import os
import sys
import types

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, _SRC)
# Stub the 'matchers' package so its heavy __init__ doesn't run, while submodule
# relative imports (fm_ontology -> .fm_ontology_data, cmms_aliases -> .fm_ontology) resolve.
_pkg = types.ModuleType("matchers")
_pkg.__path__ = [os.path.join(_SRC, "matchers")]
sys.modules["matchers"] = _pkg

F = importlib.import_module("matchers.fm_ontology")
D = importlib.import_module("matchers.fm_ontology_data")
C = importlib.import_module("matchers.cmms_aliases")


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── the generated data module is present and non-trivial ─────────────────────────
check("DOCX_FIELD_ALIASES populated", len(D.DOCX_FIELD_ALIASES) >= 100)
check("DOCX_TABLE_ALIASES populated", len(D.DOCX_TABLE_ALIASES) >= 80)
# every generated field alias targets a real canonical field
check("all docx field aliases map to a canonical field",
      all(v in C.CANONICAL_FIELDS for v in D.DOCX_FIELD_ALIASES.values()))

# ── per-platform COLUMN aliases resolve via fm_field_lookup ──────────────────────
_FIELD_CASES = {
    # SAP EAM
    "HERST": "make", "SERGE": "serial", "EQKTX": "asset_name", "EQUNR": "asset_code",
    "AUFNR": "wo_code", "PRIOK": "wo_priority", "LIFNR": "supplier", "MPNUM": "sm_code",
    "IWERK": "due_date", "KTEXT": "wo_description",
    # IBM Maximo
    "ASSETNUM": "asset_code", "MODELNUM": "model", "SERIALNUM": "serial",
    "WONUM": "wo_code", "WOPRIORITY": "wo_priority", "WORKTYPE": "wo_type",
    "REPORTDATE": "created_date", "TARGCOMPDATE": "due_date", "ACTFINISH": "last_completion_date",
    "PMNUM": "sm_code",
    # Archibus
    "wr-id": "wo_code", "wr-priority": "wo_priority", "wr-status": "wo_status",
}
for _src, _canon in _FIELD_CASES.items():
    check(f"field {_src} -> {_canon}", F.fm_field_lookup(_src)[0] == _canon)

# ── per-platform TABLE names resolve via fm_table_lookup ─────────────────────────
_TABLE_CASES = {
    "afm_sites": "site", "t001w": "site", "iflot": "site", "triProperty": "site",
    "WORKORDER": "work_order", "aufk": "work_order", "afko": "work_order", "cobie_job": "work_order",
    "EQUI": "asset", "triAsset": "asset", "equipment": "asset",
    "COMPANIES": "vendor", "LFA1": "vendor", "LFB1": "vendor",
    "LABOR": "resource", "PERNR": "resource", "people": "resource",
    "DOCLINKS": "document", "triDocument": "document",
    "CONTRACT": "contract", "ekko": "contract",
    "PM": "ppm_schedule", "procedure": "ppm_schedule",
}
for _src, _ent in _TABLE_CASES.items():
    check(f"table {_src} -> {_ent}", F.fm_table_lookup(_src)[0] == _ent)

# ── docx aliases flow through to the deterministic alias step (cmms_aliases) ─────
check("get_cmms_alias HERST -> make", C.get_cmms_alias("HERST")[0] == "make")
check("get_cmms_alias AUFNR -> wo_code", C.get_cmms_alias("AUFNR")[0] == "wo_code")
check("get_cmms_alias LIFNR -> supplier", C.get_cmms_alias("LIFNR")[0] == "supplier")

# ── curated core still wins over generated (regression vs test_fm_ontology) ──────
check("curated assetnum still asset_code", F.fm_field_lookup("assetnum")[0] == "asset_code")
check("curated property still site", F.fm_table_lookup("property")[0] == "site")
check("curated subcontractor still vendor", F.fm_table_lookup("subcontractor")[0] == "vendor")

# ── id / asset_code / barcode are SEPARATE columns → separate canonical fields ───
# (assets.id, assets.asset_code, assets.barcode are distinct columns; identity aliases
#  must NOT collapse into asset_code.)
check("id & barcode are canonical", "id" in C.CANONICAL_FIELDS and "barcode" in C.CANONICAL_FIELDS)
for _a in ("asset_id", "assetid", "equipment_id", "eq_id", "eq-id", "component_id",
           "item_id", "device_id", "plant_id", "kit_id", "installation_id"):
    check(f"identity {_a} -> id", F.fm_field_lookup(_a)[0] == "id")
for _a in ("assetnum", "asset_code", "asset_ref", "asset_no", "equnr", "installation_ref"):
    check(f"code {_a} -> asset_code", F.fm_field_lookup(_a)[0] == "asset_code")
for _a in ("barcode", "asset_barcode", "asset_tag", "rfid_tag", "tag_no"):
    check(f"tag {_a} -> barcode", F.fm_field_lookup(_a)[0] == "barcode")
check("asset_id no longer maps to asset_code", F.fm_field_lookup("asset_id")[0] != "asset_code")

print("\nALL TESTS PASSED")
